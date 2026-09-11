# -*- coding: utf-8 -*-
"""
eval/rescore.py —— S8-A 评测口径重算：对已有业务报告按"三指标分离"重算，不改任何产物。

背景（2026-09-10 纠偏，S8-A）：此前报告主要以"链内 accepted"充当业务通过，口径与
文档规定不符。本工具把一次批次重算为三个分开的指标：

- 执行完成率：尝试是否正常跑完并给出交付分级（accepted/draft/failed）；
  跳过/未执行/无分级的尝试不计完成。
- 预期行为符合率：交付等级（accepted=成品、draft=草稿、failed=无法完成）与案例
  预期（expected_outcome: final/draft/unable）是否一致；partial 预期单列，不进分母。
- 成品质量达标率：在链内判"成品"(accepted) 的尝试里，独立评测 Agent 判 accept 的
  比例、四维均值、引用可定位率、伪造/无依据标记数与禁语命中数。

诚实口径（随输出携带，不得删除）：
1) 独立评分 human_confirmed=false，是初步分，不是最终业务通过率；
2) 旧"开卷"批次（数据集关键事实曾传入写作链）的事实命中率含提示效应，
   重算只改统计口径，不能消除已获得的参考答案影响；
3) 本工具只读历史报告，不修改、不重跑、不合并任何产物。

用法：
  python -m eval.rescore --report eval/reports/gate_real_o01/business_report.json
  python -m eval.rescore --batch "eval/reports/gate_real_*" --out eval/reports/rescore_gate_batch
"""
from __future__ import annotations

import argparse
import glob
import json
import os

DEFAULT_DATASET = os.path.join("eval", "datasets", "research_writing_v1.json")

# 交付等级 ↔ 案例预期 的对应（文档规定口径；unable=链主动声明无法完成，S8 新增合法出口）
_LEVEL_TO_EXPECTED = {"accepted": "final", "draft": "draft", "failed": "unable",
                      "unable": "unable"}
_LEVEL_CN = {"accepted": "成品", "draft": "草稿", "failed": "失败",
             "unable": "无法完成"}
_EXPECTED_CN = {"final": "成品", "draft": "草稿", "unable": "无法完成", "partial": "部分完成"}
_DIMENSIONS = ("correctness", "structure", "citations", "completeness")
_GRADED_LEVELS = ("accepted", "draft", "failed", "unable")

CAVEATS = [
    "独立评测 Agent 的评分 human_confirmed=false，属初步自动评分；业务通过率以人工确认为准。",
    "开卷提示效应：旧批次把数据集关键事实作为任务要求传入写作链，事实命中率的提升含提示效应；"
    "本重算只改统计口径，不能消除旧运行已获得参考答案的影响。",
    "本工具只读历史报告，不修改、不重跑、不合并任何产物；新旧口径数字不可直接相加或平均。",
]


def load_expectations(dataset_path: str) -> dict[str, str]:
    """从数据集取 id → expected_outcome（final/draft/unable/partial）。"""
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)
    return {t["id"]: t.get("expected_outcome", "final") for t in data.get("tasks", [])}


def load_task_attributes(dataset_path: str) -> tuple[dict, dict, dict]:
    """从数据集取 (期望交付, 机制标签, batch) 三个 id→值 映射，供分组统计。"""
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)
    expectations, mechanisms, batches = {}, {}, {}
    for t in data.get("tasks", []):
        expectations[t["id"]] = t.get("expected_outcome", "final")
        mechanisms[t["id"]] = list(t.get("mechanisms") or [])
        batches[t["id"]] = str(t.get("batch") or "v1")
    return expectations, mechanisms, batches


def rescore_report(report: dict, expectations: dict[str, str],
                   mechanisms: dict[str, list[str]] | None = None,
                   batches: dict[str, str] | None = None) -> dict:
    """对单份 business_report.json 按三指标分离重算；只读，不改输入。

    mechanisms/batches 提供时，行上带分组字段，totals 输出分机制/分批次统计。
    """
    mechanisms = mechanisms or {}
    batches = batches or {}
    # ingest（人工确认）写入 report["records"]，与 results 并行存在；按 (id, attempt) 回退取 human
    human_by_key: dict[tuple, dict] = {}
    for rec in report.get("records", []):
        try:
            human_by_key[(str(rec.get("id")), int(rec.get("attempt") or 1))] = \
                rec.get("human") or {}
        except (TypeError, ValueError):
            continue
    rows: list[dict] = []
    for att in report.get("results", []):
        aid = att.get("id", "?")
        expected = expectations.get(aid, "final")
        level = att.get("draft_level")
        executed = level in _GRADED_LEVELS
        grader = att.get("grader") or {}
        mc = att.get("machine_checks") or {}
        human = att.get("human") or human_by_key.get(
            (str(aid), int(att.get("attempt") or 1)), {})
        delivered = _LEVEL_TO_EXPECTED.get(level)
        row = {
            "id": aid,
            "category": att.get("category", ""),
            "expected_outcome": expected,
            "expected_cn": _EXPECTED_CN.get(expected, expected),
            "delivered_level": level,
            "delivered_cn": _LEVEL_CN.get(level, "未交付"),
            "executed": executed,
            "level_match": bool(executed and delivered == expected),
            "partial_expected": expected == "partial",
            "mechanisms": mechanisms.get(aid, []),
            "batch": batches.get(aid, "v1"),
            "grader_verdict": grader.get("overall_verdict"),
            "grader_accept": grader.get("overall_verdict") == "accept",
            "human_scored": bool(human),
            "human_verdict": human.get("human_verdict"),
            "human_accept": human.get("human_verdict") == "accept",
            "human_confirmed": bool(grader.get("human_confirmed")) or bool(human),
            "dimension_means": _dim_means(grader),
            "citation_tokens": mc.get("citation_tokens", 0),
            "unresolved_citations": mc.get("unresolved_citations", 0),
            "fabrication_flags": len(grader.get("fabrication_flags") or []),
            "program_problems": len(grader.get("program_problems") or []),
            "forbidden_claim_hits": mc.get("forbidden_claim_hits", 0),
            "estimated_cost_usd": att.get("estimated_cost_usd"),
            "termination_reason": att.get("termination_reason", ""),
        }
        rows.append(row)
    return {"meta": _meta_of(report), "rows": rows, "totals": _totals(rows)}


def _meta_of(report: dict) -> dict:
    meta = report.get("meta", {}) or {}
    versions = (meta.get("versions") or {}).get("code_revision", "")
    return {"name": meta.get("name", ""), "mode": meta.get("mode", ""),
            "generated_at": meta.get("generated_at", ""), "code_revision": versions}


def _dim_means(grader: dict) -> dict:
    dims = (grader or {}).get("dimensions") or {}
    out = {}
    for name in _DIMENSIONS:
        score = (dims.get(name) or {}).get("score")
        out[name] = score if isinstance(score, (int, float)) else None
    return out


def _totals(rows: list[dict]) -> dict:
    total = len(rows)
    executed_rows = [r for r in rows if r["executed"]]
    conformance_rows = [r for r in rows if r["executed"] and not r["partial_expected"]]
    matched = [r for r in conformance_rows if r["level_match"]]
    accepted = [r for r in rows if r["delivered_level"] == "accepted"]
    accepted_grader_accept = [r for r in accepted if r["grader_accept"]]

    # 引用可定位率：只统计链内 accepted 的尝试；无任何引用记 None（未知），不记 100%
    tokens = sum(r["citation_tokens"] for r in accepted)
    unresolved = sum(r["unresolved_citations"] for r in accepted)
    locate_rate = (1 - unresolved / tokens) if tokens > 0 else None

    means: dict[str, float | None] = {}
    for name in _DIMENSIONS:
        vals = [r["dimension_means"][name] for r in accepted
                if r["dimension_means"].get(name) is not None]
        means[name] = round(sum(vals) / len(vals), 2) if vals else None

    return {
        "attempts_total": total,
        "executed": len(executed_rows),
        "execution_completion_rate": round(len(executed_rows) / total, 4) if total else None,
        "conformance_denominator": len(conformance_rows),
        "expected_match": len(matched),
        "expected_behavior_conformance_rate":
            round(len(matched) / len(conformance_rows), 4) if conformance_rows else None,
        "partial_single_listed": [r["id"] for r in rows if r["partial_expected"]],
        "chain_accepted_ids": [r["id"] for r in accepted],
        "quality_grader_accept_rate":
            round(len(accepted_grader_accept) / len(accepted), 4) if accepted else None,
        "dimension_means_accepted": means,
        "citation_locate_rate": round(locate_rate, 4) if locate_rate is not None else None,
        "chain_accepted_with_fabrication_flags":
            sum(1 for r in accepted if r["fabrication_flags"] > 0),
        "chain_accepted_fabrication_flags_total":
            sum(r["fabrication_flags"] for r in accepted),
        "chain_accepted_forbidden_hits": sum(r["forbidden_claim_hits"] for r in accepted),
        "by_mechanism": _group_by_mechanism(rows),
        "by_batch": _group_by_batch(rows),
        "human_scored": sum(1 for r in rows if r["human_scored"]),
        "human_accept": sum(1 for r in rows if r["human_scored"] and r["human_accept"]),
        "human_accept_rate": (
            round(sum(1 for r in rows if r["human_scored"] and r["human_accept"])
                  / sum(1 for r in rows if r["human_scored"]), 4)
            if any(r["human_scored"] for r in rows) else None),
        "human_accept_of_accepted": sum(1 for r in accepted
                                        if r["human_scored"] and r["human_accept"]),
        "human_confirmed": all(r["human_confirmed"] for r in rows) if rows else False,
        "caveats": list(CAVEATS),
    }


def _human_line(t: dict) -> str:
    if not t.get("human_scored"):
        return "人工确认口径：未导入（读数仅为独立评测初步分，human_confirmed=false）"
    return (f"人工确认口径（权威通过率）：已评 {t['human_scored']} 例，人工 accept "
            f"{t['human_accept']} = {_fmt(t['human_accept_rate'])}；"
            f"链内 accepted 中人工 accept {t['human_accept_of_accepted']}/"
            f"{len(t.get('chain_accepted_ids', []))}")


def _group_stats(rows: list[dict]) -> dict:
    """一组行的小统计：完成/符合/质量（独立评测与人工确认两口径并列）。"""
    executed = [r for r in rows if r["executed"]]
    conf = [r for r in executed if not r["partial_expected"]]
    matched = [r for r in conf if r["level_match"]]
    acc = [r for r in rows if r["delivered_level"] == "accepted"]
    acc_accept = [r for r in acc if r["grader_accept"]]
    scored = [r for r in rows if r["human_scored"]]
    human_accept = [r for r in scored if r["human_accept"]]
    acc_human_accept = [r for r in acc if r["human_scored"] and r["human_accept"]]
    return {"cases": len(rows), "executed": len(executed),
            "expected_match": len(matched),
            "conformance_rate": round(len(matched) / len(conf), 4) if conf else None,
            "chain_accepted": len(acc), "grader_accept_of_accepted": len(acc_accept),
            "quality_accept_rate": round(len(acc_accept) / len(acc), 4) if acc else None,
            "human_scored": len(scored), "human_accept": len(human_accept),
            "human_accept_rate": round(len(human_accept) / len(scored), 4) if scored else None,
            "human_accept_of_accepted": len(acc_human_accept)}


def _group_by_mechanism(rows: list[dict]) -> dict:
    """分机制统计：一例可属多机制，各机制独立计数（分母为该机制的案例数）。"""
    tags: dict[str, list[dict]] = {}
    for r in rows:
        for tag in r.get("mechanisms") or ["未标注"]:
            tags.setdefault(tag, []).append(r)
    return {tag: _group_stats(rs) for tag, rs in sorted(tags.items())}


def _group_by_batch(rows: list[dict]) -> dict:
    tags: dict[str, list[dict]] = {}
    for r in rows:
        tags.setdefault(r.get("batch") or "v1", []).append(r)
    return {tag: _group_stats(rs) for tag, rs in sorted(tags.items())}


def rescore_batch(report_paths: list[str], expectations: dict[str, str],
                  mechanisms: dict[str, list[str]] | None = None,
                  batches: dict[str, str] | None = None) -> dict:
    """聚合多份报告：逐份重算后跨批合计（合计只在同口径行上相加）。"""
    per_report = []
    all_rows: list[dict] = []
    for path in report_paths:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
        res = rescore_report(report, expectations, mechanisms, batches)
        res["meta"]["report_path"] = path
        per_report.append(res)
        all_rows.extend(res["rows"])
    batch = {"reports": per_report, "totals": _totals(all_rows)}
    batch["totals"]["report_count"] = len(per_report)
    batch["totals"]["caveats"] = list(CAVEATS)
    return batch


def render_markdown(res: dict) -> str:
    t = res["totals"]
    lines = ["# 口径重算（S8-A：三指标分离）", ""]
    m = res.get("meta", {})
    if m.get("name"):
        lines += [f"- 数据集：{m['name']}　模式：{m.get('mode', '')}　"
                  f"生成时间：{m.get('generated_at', '')}　代码版本：{m.get('code_revision', '')}", ""]
    lines += [
        "| 指标 | 数值 |", "|---|---|",
        f"| 执行完成率 | {t['executed']}/{t['attempts_total']} = {_fmt(t['execution_completion_rate'])} |",
        f"| 预期行为符合率 | {t['expected_match']}/{t['conformance_denominator']} = "
        f"{_fmt(t['expected_behavior_conformance_rate'])}（partial 单列："
        f"{', '.join(t['partial_single_listed']) or '无'}） |",
        f"| 成品质量达标率（链内 accepted 中独立评测 accept） | "
        f"{_quality_counts(t)} = {_fmt(t['quality_grader_accept_rate'])} |",
        f"| 引用可定位率（链内 accepted） | {_fmt(t['citation_locate_rate'])} |",
        f"| 链内 accepted 四维均值（正确性/结构/引用/完整性） | {_dims_cn(t)} |",
        f"| 链内 accepted 带伪造/无依据标记 | {t['chain_accepted_with_fabrication_flags']} 例 / "
        f"{t['chain_accepted_fabrication_flags_total']} 条 |",
        f"| 链内 accepted 禁语命中 | {t['chain_accepted_forbidden_hits']} 次 |",
        "",
        f"**{_human_line(t)}**",
        "",
        "## 诚实口径", "",
        *[f"- {c}" for c in t.get("caveats", CAVEATS)], "",
    ]
    human_cols = bool(t.get("human_scored"))
    if t.get("by_mechanism"):
        lines += ["## 分机制比例（一例可属多机制，分母=该机制案例数）", "",
                  "| 机制 | 案例 | 执行 | 预期符合 | 链内accepted | 独立accept | 质量accept率 | "
                  + ("人工accept | 人工accept率 |" if human_cols else ""),
                  "|---|---|---|---|---|---|---|" + ("---|---|" if human_cols else "")]
        for tag, g in t["by_mechanism"].items():
            cells = [tag, g["cases"], g["executed"], g["expected_match"],
                     g["chain_accepted"], g["grader_accept_of_accepted"],
                     _fmt(g["quality_accept_rate"])]
            if human_cols:
                cells += [g.get("human_accept", 0), _fmt(g.get("human_accept_rate"))]
            lines.append("| " + " | ".join(str(c) for c in cells) + " |")
        lines.append("")
    if t.get("by_batch"):
        lines += ["## 分批次（batch）", "",
                  "| batch | 案例 | 执行 | 预期符合 | 链内accepted | 独立accept | 质量accept率 |",
                  "|---|---|---|---|---|---|---|"]
        for tag, g in t["by_batch"].items():
            lines.append(f"| {tag} | {g['cases']} | {g['executed']} | {g['expected_match']} "
                         f"| {g['chain_accepted']} | {g['grader_accept_of_accepted']} "
                         f"| {_fmt(g['quality_accept_rate'])} |")
        lines.append("")
    if res.get("reports"):  # 批次模式：附逐报告小表
        lines += ["## 逐报告", "", "| 报告 | 完成 | 符合 | 链内accepted→独立accept |", "|---|---|---|---|"]
        for r in res["reports"]:
            rt = r["totals"]
            lines.append(f"| {r['meta'].get('report_path', '')} | {rt['executed']}/{rt['attempts_total']} "
                         f"| {rt['expected_match']}/{rt['conformance_denominator']} "
                         f"| {_quality_counts(rt)} |")
        lines.append("")
    lines += ["## 逐案例", "", "| id | 预期 | 交付 | 一致 | 独立评测 | 引用(未解析/总数) | 伪造标记 | 禁语 |",
              "|---|---|---|---|---|---|---|---|"]
    rows = res.get("rows") or [row for rep in res.get("reports", []) for row in rep["rows"]]
    for r in rows:
        lines.append(f"| {r['id']} | {r['expected_cn']}{'(单列)' if r['partial_expected'] else ''} "
                     f"| {r['delivered_cn']} | {'√' if r['level_match'] else '×'} "
                     f"| {r['grader_verdict'] or '—'} | {r['unresolved_citations']}/{r['citation_tokens']} "
                     f"| {r['fabrication_flags']} | {r['forbidden_claim_hits']} |")
    return "\n".join(lines) + "\n"


def _quality_counts(t: dict) -> str:
    accepted_n = len(t.get("chain_accepted_ids", []))
    rate = t.get("quality_grader_accept_rate")
    if rate is None:
        return f"0/0"
    return f"{round(rate * accepted_n)}/{accepted_n}"


def _dims_cn(t: dict) -> str:
    m = t.get("dimension_means_accepted", {})
    return " / ".join(str(m.get(k)) if m.get(k) is not None else "—" for k in _DIMENSIONS)


def _fmt(v):
    return "—" if v is None else f"{v * 100:.1f}%" if isinstance(v, float) else str(v)


def main() -> int:
    parser = argparse.ArgumentParser(description="S8-A 口径重算：三指标分离，只读历史报告")
    parser.add_argument("--report", help="单份 business_report.json")
    parser.add_argument("--batch", help="报告目录通配（每个目录含 business_report.json）")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="数据集（取 expected_outcome）")
    parser.add_argument("--out", default="", help="输出前缀（生成 <out>.json 与 <out>.md）")
    parser.add_argument("--report-name", default="business_report.json",
                        help="batch 模式下每个目录要读的报告文件名"
                             "（如 business_report_human.json = 人工确认口径）")
    args = parser.parse_args()
    if not args.report and not args.batch:
        parser.error("需要 --report 或 --batch 之一")

    expectations = load_expectations(args.dataset)
    _, mechanisms, batches = load_task_attributes(args.dataset)
    if args.batch:
        paths = sorted(os.path.join(d, args.report_name)
                       for d in glob.glob(args.batch)
                       if os.path.exists(os.path.join(d, args.report_name)))
        if not paths:
            parser.error(f"批匹配无结果：{args.batch}")
        res = rescore_batch(paths, expectations, mechanisms, batches)
    else:
        with open(args.report, encoding="utf-8") as f:
            res = rescore_report(json.load(f), expectations, mechanisms, batches)

    out_prefix = args.out or os.path.splitext(args.report or args.batch)[0] + "_rescore"
    with open(out_prefix + ".json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    with open(out_prefix + ".md", "w", encoding="utf-8", newline="\n") as f:
        f.write(render_markdown(res))
    t = res["totals"]
    print(f"重算完成 → {out_prefix}.json / .md")
    print(f"执行完成 {t['executed']}/{t['attempts_total']}；"
          f"预期行为符合 {t['expected_match']}/{t['conformance_denominator']}"
          f"（partial 单列 {len(t['partial_single_listed'])}）；"
          f"链内 accepted {len(t['chain_accepted_ids'])} 例中独立评测 accept "
          f"= {_quality_counts(t)}（human_confirmed=false）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
