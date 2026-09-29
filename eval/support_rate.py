# -*- coding: utf-8 -*-
"""
eval/support_rate.py —— 语义支持率测量（批次 C；主计划 §8.1"语义支持率 ≥95%"首次口径落地）

定位与诚实边界：
- 对已有 business_report 的最终报告逐句解析 [E-xxx] 引用，回溯该任务 evidence.json，
  由独立评测模型判定"该证据是否语义支持该断言"（supported/partial/unsupported/unrelated）；
- 判定是模型语义判断而非程序事实：脚本只做结构解析、调用与汇总，判分质量需人工抽检校准；
- support_rate 是测量读数，不构成任何验收结论；建议评测模型 ≠ 写作模型（同模型时如实记录）；
- strict_rate = supported/总判定（partial 不计入）；lenient_rate = (supported+partial)/总判定。

运行（可多次 --report，跨批次合并判分；结果带缓存，重复运行不重复计费）：
    python -m eval.support_rate --report eval/reports/v3_public_batch/business_report.json \
        --report eval/reports/o21_verify/r03/business_report.json \
        --mode real --max-cost 0.6 --out eval/reports/support_rate_batchC
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from config.settings import Settings
from src.harness.model_gateway import BudgetStop, JobLedger, job_scope, model_call
from src.application.request import TaskRequest

CITE_RE = re.compile(r"[\[【（(]\s*(E-\d{3})\s*[\]】）)]")
_SENT_SPLIT = re.compile(r"(?<=[。！？；\n])")
_MAX_UNIT_CHARS = 160
_VERDICTS = ("supported", "partial", "unsupported", "unrelated")
_QUOTE_TRUNC = 220
# 非断言单元（人工校准 2026-09-29 发现的测量污染，不建对不判分）：
# ① 纯引用簇——逗号切分把断言正文与引用分离后只剩引用标记与标点；
# ② 簿记行——报告附录的"来源定位：…标【E-xxx】的条目"机械清单，非内容断言
_BARE_CITATION = re.compile(r"^[\s\-•·【\[\]（）()、，。,\.E0-9〕〔]+$")
_BOOKKEEPING = re.compile(r"^[-•·\s]*来源定位")


def unit_kind(unit: str) -> str:
    """assertion=内容断言（判分）；citation_only/bookkeeping=非断言（跳过并计数）。"""
    if _BOOKKEEPING.search(unit):
        return "bookkeeping"
    bare = CITE_RE.sub("", unit)
    bare = bare.replace("〔推断〕", "").replace("〔事实〕", "").replace("〔未知〕", "")
    # 阈值 4：真正的纯引用簇剥完标记后内容字为 0；短事实句（如"乙未提供报价"）必须保留
    if len(re.sub(r"[\s\-•·【\[\]（）()、，。,\.〕〔]", "", bare)) < 4:
        return "citation_only"
    return "assertion"


def extract_array(text: str) -> list | None:
    """从回复提取 JSON 数组（容忍 ```json 围栏、前后杂文与 {"results":[...]} 包装）。

    harness.structured.extract_json 只认对象；本脚本的判定输出是顶层数组，
    故在脚本内做数组容错解析，不改共享解析器的契约。
    """
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    if text.lstrip().startswith("{"):
        try:
            wrapped = json.loads(text.strip())
            if isinstance(wrapped, dict) and isinstance(wrapped.get("results"), list):
                return wrapped["results"]
        except json.JSONDecodeError:
            pass
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for raw in candidates:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            continue
    return None

JUDGE_SYSTEM = (
    "你是独立的引用支持性判定员。对每条「断言 + 证据」对，判定证据是否在语义上支持该断言：\n"
    "- supported：证据直接支持断言的核心内容；\n"
    "- partial：证据与断言相关，但断言含超出证据的引申或强化；\n"
    "- unsupported：证据与断言主题相关，但不包含断言所陈述的内容；\n"
    "- unrelated：证据与断言几乎无关。\n"
    "只依据给出的证据摘录判断，禁止使用任何外部知识；断言来自基于该材料撰写的报告，"
    "断言中的〔事实〕〔推断〕标注不代表证据强度。"
    "输出 JSON 数组：每项 {\"id\":\"<原id>\",\"verdict\":\"四选一\",\"reason\":\"≤30字\"}；"
    "id 必须与输入一一对应，不得增删。")


def split_units(text: str) -> list[str]:
    """把报告正文切成判定单元（句/长句再按逗号切），保留引用标记所在的最小语境。"""
    units: list[str] = []
    for raw in _SENT_SPLIT.split(text or ""):
        unit = raw.strip()
        if not unit:
            continue
        unit = unit.lstrip("#").strip()
        if len(unit) <= _MAX_UNIT_CHARS:
            units.append(unit)
            continue
        buf = ""
        for part in re.split(r"(?<=[，、])", unit):
            if len(buf) + len(part) > _MAX_UNIT_CHARS and buf:
                units.append(buf)
                buf = part
            else:
                buf += part
        if buf:
            units.append(buf)
    return units


def extract_citations(unit: str) -> list[str]:
    seen: list[str] = []
    for m in CITE_RE.finditer(unit):
        eid = m.group(1)
        if eid not in seen:
            seen.append(eid)
    return seen


def pair_id(job_id: str, unit: str, evidence_id: str) -> str:
    return hashlib.sha1(f"{job_id}|{unit}|{evidence_id}".encode("utf-8")).hexdigest()[:12]


def build_pairs(report_path: Path) -> tuple[list[dict], list[dict], dict]:
    """从 business_report 收集 (断言, 证据) 判定对。

    返回 (pairs, missing_evidence, skipped)——skipped 为按 unit_kind 跳过的
    非断言单元计数（citation_only/bookkeeping），不建对、不判分、如实上报。
    """
    report = json.loads(report_path.read_text(encoding="utf-8"))
    base = report_path.parent
    pairs: list[dict] = []
    missing: list[dict] = []
    skipped = {"citation_only": 0, "bookkeeping": 0}
    for row in report.get("results", []):
        final_text = row.get("final_text_head")
        job_id = row.get("root_job_id")
        if not final_text or not job_id:
            continue
        # 行内若无全文，读 job.json 的 final_text（business_report 只存 head）
        job_json = base / "workspace" / "jobs" / job_id / "job.json"
        if job_json.exists():
            final_text = json.loads(job_json.read_text(encoding="utf-8")).get("final_text") or final_text
        evidence_path = base / "workspace" / "jobs" / job_id / "evidence.json"
        if not evidence_path.exists():
            missing.append({"case_id": row.get("id"), "job_id": job_id,
                            "reason": "evidence.json 不存在"})
            continue
        evidence = {e["evidence_id"]: e for e in
                    json.loads(evidence_path.read_text(encoding="utf-8")).get("items", [])}
        for unit in split_units(final_text):
            kind = unit_kind(unit)
            if kind != "assertion":
                skipped[kind] += 1
                continue
            for eid in extract_citations(unit):
                item = evidence.get(eid)
                if item is None:
                    missing.append({"case_id": row.get("id"), "job_id": job_id,
                                    "evidence_id": eid, "reason": "引用的证据不在 evidence.json"})
                    continue
                pairs.append({
                    "id": pair_id(job_id, unit, eid),
                    "case_id": row.get("id"), "job_id": job_id,
                    "unit": unit, "evidence_id": eid,
                    "quote": str(item.get("quote", ""))[:_QUOTE_TRUNC],
                    "fact": str(item.get("fact", ""))[:80],
                    "tag": item.get("tag", "")})
    return pairs, missing, skipped


def _batch_prompt(batch: list[dict]) -> str:
    items = [{"id": p["id"], "断言": p["unit"],
              "证据摘录（原文逐字）": p["quote"], "证据要点": p["fact"]} for p in batch]
    return "待判定条目：\n" + json.dumps(items, ensure_ascii=False, indent=1)


def judge_pairs(llm, pairs: list[dict], *, ledger_dir: Path, max_cost: float | None,
                batch_size: int = 8, cached: dict | None = None) -> tuple[dict, dict]:
    """批量判定；返回 (verdicts{id: record}, usage)。缓存命中不重复调用。"""
    verdicts: dict[str, dict] = {k: v for k, v in (cached or {}).items()
                                 if v.get("verdict") in _VERDICTS}
    todo = [p for p in pairs if p["id"] not in verdicts]
    request = TaskRequest(task="语义支持率判分（批次 C）", mode="real",
                          max_calls=max(1, (len(todo) + batch_size - 1) // batch_size + 2),
                          max_output_tokens=65536, max_seconds=1800,
                          max_cost=max_cost)
    ledger = JobLedger(ledger_dir, request)
    usage = {"call_count": 0, "estimated_cost_usd": 0.0, "budget_stopped": False,
             "batches": 0, "invalid_records": 0, "unparsable_batches": 0}
    try:
        with job_scope(ledger):
            for start in range(0, len(todo), batch_size):
                batch = todo[start:start + batch_size]
                messages = [
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": _batch_prompt(batch)},
                ]
                try:
                    reply = model_call(llm, messages, purpose="support_rate_judge",
                                       role="grader", disable_thinking=True)
                except BudgetStop:
                    usage["budget_stopped"] = True
                    break
                usage["batches"] += 1
                parsed = extract_array(reply.content)
                by_id = {}
                if parsed is None:
                    # 整批解析失败如实计数（首跑 71 批全空即本盲区所致）
                    usage["unparsable_batches"] = usage.get("unparsable_batches", 0) + 1
                else:
                    for rec in parsed:
                        if (isinstance(rec, dict) and rec.get("id") in {p["id"] for p in batch}
                                and rec.get("verdict") in _VERDICTS):
                            by_id[rec["id"]] = {
                                "verdict": rec["verdict"],
                                "reason": str(rec.get("reason", ""))[:60]}
                        else:
                            usage["invalid_records"] += 1
                for p in batch:
                    if p["id"] in by_id:
                        verdicts[p["id"]] = by_id[p["id"]]
    finally:
        ledger.finish("completed" if not usage["budget_stopped"] else "partial")
    summary = ledger.summary()
    usage["call_count"] = summary["call_count"]
    usage["estimated_cost_usd"] = summary["estimated_cost_usd"]
    usage["unknown_usage_calls"] = summary["unknown_usage_calls"]
    return verdicts, usage


def aggregate(pairs: list[dict], verdicts: dict[str, dict]) -> dict:
    rows, strict_n = [], 0
    strict_hits = lenient_hits = 0
    per_case: dict[str, dict] = {}
    dist: dict[str, int] = {}
    unjudged = 0
    for p in pairs:
        rec = verdicts.get(p["id"])
        if rec is None:
            unjudged += 1
            continue
        v = rec["verdict"]
        dist[v] = dist.get(v, 0) + 1
        strict_n += 1
        if v == "supported":
            strict_hits += 1
        if v in ("supported", "partial"):
            lenient_hits += 1
        case = per_case.setdefault(p["case_id"], {"judged": 0, "supported": 0})
        case["judged"] += 1
        if v == "supported":
            case["supported"] += 1
    return {
        "total_pairs": len(pairs), "judged": strict_n, "unjudged": unjudged,
        "verdict_distribution": dist,
        "strict_support_rate": round(strict_hits / strict_n, 4) if strict_n else None,
        "lenient_support_rate": round(lenient_hits / strict_n, 4) if strict_n else None,
        "per_case": {k: {**v, "case_rate": round(v["supported"] / v["judged"], 3)
                         if v["judged"] else None}
                     for k, v in sorted(per_case.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="语义支持率测量（批次 C）")
    parser.add_argument("--report", action="append", required=True,
                        help="business_report.json 路径，可多次传入合并判分")
    parser.add_argument("--mode", choices=("mock", "real"), default="real")
    parser.add_argument("--max-cost", type=float, default=None,
                        help="判分整批美元估算上限（真实模式必填）")
    parser.add_argument("--model", default=None,
                        help="判定模型（默认 GRADER_MODEL_NAME，再默认主模型）")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--out", default="eval/reports/support_rate")
    args = parser.parse_args()
    if args.mode == "real" and not (isinstance(args.max_cost, (int, float)) and args.max_cost >= 0):
        parser.error("真实模式必须给非负 --max-cost（判分整批估算上限）")
    settings = Settings()
    from eval.grader import build_grader_llm
    llm, used_model = build_grader_llm(args.mode, settings, args.model)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    pairs: list[dict] = []
    missing: list[dict] = []
    skipped = {"citation_only": 0, "bookkeeping": 0}
    seen_ids = set()
    for rp in args.report:
        p, m, sk = build_pairs(Path(rp))
        for item in p:
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                pairs.append(item)
        missing.extend(m)
        for k in skipped:
            skipped[k] += sk[k]
    cache_path = out / "verdicts.json"
    cached = {}
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))

    ledger_dir = out / "workspace" / ("job_" + hashlib.sha1(
        "|".join(sorted(seen_ids)).encode("utf-8")).hexdigest()[:32])
    verdicts, usage = judge_pairs(
        llm, pairs, ledger_dir=ledger_dir,
        max_cost=args.max_cost if args.mode == "real" else None,
        batch_size=args.batch_size, cached=cached)
    summary = aggregate(pairs, verdicts)
    result = {
        "meta": {"generated_by": "eval.support_rate", "mode": args.mode,
                 "judge_model": used_model,
                 "independence": "same_model_as_writer"
                 if used_model == settings.model_name else "independent_grader_model",
                 "reports": args.report, "note": "测量读数，不构成验收结论；判分需人工抽检校准"},
        "usage": usage, "missing_evidence": missing, "skipped_units": skipped,
        "pairs": pairs, "verdicts": verdicts, "summary": summary}
    (out / "support_rate_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    cache_path.write_text(json.dumps(verdicts, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    print(json.dumps({"meta": result["meta"], "usage": usage,
                      "summary": {k: v for k, v in summary.items() if k != "per_case"}},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
