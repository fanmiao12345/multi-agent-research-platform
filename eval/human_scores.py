# -*- coding: utf-8 -*-
"""
eval/human_scores.py —— 人工评分表（S6-05）

- 从 business_report.json 生成 CSV 评分表：每行一次尝试；
- 机器检查列明确标注"程序检查（仅供参考）"，正确性/结构/引用/完整性按 1~5 人工评分，
  记录人工改稿时间；
- 模型/评测器可以辅助找问题，但绝不自评分自动盖章：只有人工填写并导入后才更新
  业务通过率（human_ingest 入口在试用期按需补充）。

运行：python -m eval.human_scores --report <business_report.json> --out <目录>
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

CRITERIA = ["correctness", "structure", "citations", "completeness"]
CRITERION_LABELS = {"correctness": "正确性(1-5)", "structure": "结构(1-5)",
                    "citations": "引用(1-5)", "completeness": "完整性(1-5)"}
INSTRUCTIONS = (
    "评分说明：1=不可用 2=大量错误 3=可用但有明显不足 4=满足要求 5=可直接交付。"
    "correctness 需人工核对关键事实与禁止项；机器检查列仅供参考，不得作为自动盖章依据。"
    "填写方式：每个维度行第 3 列填入 1~5；revised_minutes 行第 3 列填人工改稿分钟数；"
    "其余列请勿改动。revised_minutes 记录你本次人工改稿花费的分钟数。"
)


def build_sheets(report: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets = []
    for case in report.get("records", []):
        checks = case.get("machine_checks", {})
        sheet = out_dir / f"{case['id']}_rep{case.get('attempt', 1)}_scores.csv"
        with sheet.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["# " + INSTRUCTIONS])
            writer.writerow(["case_id", case["id"], "attempt", case.get("attempt"),
                             "draft_level", case.get("draft_level", "")])
            for key, label in CRITERION_LABELS.items():
                writer.writerow([key, label, "", "", "备注", ""])
            writer.writerow(["revised_minutes", "人工改稿分钟", "", "", "备注", ""])
            writer.writerow(["machine_citation_tokens", checks.get("citation_tokens"),
                             "机器检查", ""])
            writer.writerow(["machine_unresolved_citations",
                             checks.get("unresolved_citations"), "机器检查", ""])
            writer.writerow(["machine_sections", f"{checks.get('section_hits')}/"
                             f"{checks.get('required_sections')}", "机器检查", ""])
            writer.writerow(["machine_facts", f"{checks.get('fact_hits')}/"
                             f"{checks.get('fact_total')}", "机器检查", ""])
            writer.writerow(["machine_forbidden_hits",
                             checks.get("forbidden_claim_hits"), "机器检查",
                             "；".join(checks.get("forbidden_claims", []))])
            writer.writerow(["termination_reason", case.get("termination_reason"),
                             "程序记录", ""])
            writer.writerow(["elapsed_seconds", case.get("elapsed_seconds"), "程序记录", ""])
            writer.writerow(["estimated_cost_usd", case.get("estimated_cost_usd"),
                             "程序记录", ""])
            writer.writerow(["message", case.get("message", ""), "程序记录", ""])
        sheets.append(sheet)
    return sheets


def ingest_sheets(report: dict, sheets_dir: Path) -> dict:
    """读取人工填写的评分表（第3列为 1~5 / 改稿分钟），合并回报告副本。

    返回更新后的 report（records 增加 human 段；grader 段标记 human_confirmed=True 表示已人工复核）。
    """
    import copy
    import datetime as _dt
    updated = copy.deepcopy(report)
    by_key = {}
    for record in updated.get("records", []):
        by_key[(str(record.get("id")), int(record.get("attempt") or 1))] = record
    applied = 0
    for sheet in sorted(Path(sheets_dir).glob("*_scores.csv")):
        rows = []
        with sheet.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        case_id, attempt = None, 1
        dims: dict[str, int] = {}
        revised_minutes = None
        for row in rows:
            if not row:
                continue
            key = (row[0] or "").strip()
            if key == "case_id" and len(row) >= 2:
                case_id = row[1].strip()
                if len(row) >= 4 and str(row[3]).strip().isdigit():
                    attempt = int(row[3])
            elif key in CRITERIA and len(row) >= 3:
                value = str(row[2]).strip()
                if value.isdigit():
                    score = int(value)
                    if 1 <= score <= 5:
                        dims[key] = score
                    else:
                        raise ValueError(f"{case_id}#{attempt} 维度 {key} 分数越界：{score}")
            elif key == "revised_minutes" and len(row) >= 3:
                value = str(row[2]).strip()
                if value.isdigit():
                    revised_minutes = int(value)
        if case_id is None or not dims:
            continue
        record = by_key.get((case_id, attempt))
        if record is None:
            continue
        verdict = "accept" if all(dims[d] >= 4 for d in CRITERIA) else "draft"
        record["human"] = {
            "dimensions": dims,
            "revised_minutes": revised_minutes,
            "human_verdict": verdict,
            "confirmed_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "note": "人工评分（覆盖/确认 Agent 初步评分）"}
        if isinstance(record.get("grader"), dict):
            record["grader"]["human_confirmed"] = True
            record["grader"]["human_verdict"] = verdict
        applied += 1
    updated["meta"] = dict(updated.get("meta", {}))
    updated["meta"]["human_confirm"] = {
        "applied": applied,
        "accept_human": sum(1 for r in updated["records"]
                            if isinstance(r.get("human"), dict)
                            and r["human"]["human_verdict"] == "accept"),
        "graded_human": applied,
        "note": "已人工确认的分数可以用于业务通过率计算（human_confirmed=true）"}
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="人工评分表与人工确认入口（S6-05）")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("sheet", help="从 business_report.json 生成评分表")
    gen.add_argument("--report", required=True)
    gen.add_argument("--out", default="eval/reports/business/sheets")
    ing = sub.add_parser("ingest", help="读取人工填写的评分表并合并（human_confirmed=true）")
    ing.add_argument("--sheets", required=True, help="评分表目录（*_scores.csv）")
    ing.add_argument("--report", required=True)
    ing.add_argument("--out", required=True, help="合并后报告输出路径（json）")
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    if args.command == "sheet":
        sheets = build_sheets(report, Path(args.out))
        print(INSTRUCTIONS)
        print(f"生成 {len(sheets)} 张评分表：{Path(args.out)}")
        for sheet in sheets[:5]:
            print("-", sheet)
        return
    updated = ingest_sheets(report, Path(args.sheets))
    Path(args.out).write_text(json.dumps(updated, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    summary = updated["meta"]["human_confirm"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"合并后报告：{args.out}")


if __name__ == "__main__":
    main()
