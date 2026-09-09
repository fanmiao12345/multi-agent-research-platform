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
    "revised_minutes 记录你本次人工改稿花费的分钟数。"
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
                writer.writerow([key, label, "score(1-5)", "", "备注", ""])
            writer.writerow(["revised_minutes", "人工改稿分钟", "", ""])
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--out", default="eval/reports/business/sheets")
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    sheets = build_sheets(report, Path(args.out))
    print(INSTRUCTIONS)
    print(f"生成 {len(sheets)} 张评分表：{Path(args.out)}")
    for sheet in sheets[:5]:
        print("-", sheet)


if __name__ == "__main__":
    main()
