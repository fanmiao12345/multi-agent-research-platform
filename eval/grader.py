# -*- coding: utf-8 -*-
"""
eval/grader.py —— 专职评测 Agent（代替人工初步评分，S6-05 扩展）

定位与诚实边界：
- 用独立评测模型按明确评分表（正确性/结构/引用/完整性 1~5）对报告打分并给依据；
- 程序层交叉核验评分者声称：维度分钳位、引用 id 必须真实存在于报告与证据、
  禁语/伪造标记复查、computed_verdict 以规则重算为准（不信模型自报的 verdict 盖章）；
- 产出固定标记 grader:"agent"、human_confirmed:false —— 供人工审计与覆盖
  （任何"最终业务验收"仍需要人工确认或显式策略放行，避免自评自盖章被滥用）；
- 建议：评测模型与被评任务用不同 profile/实例；相同模型时 meta 记录
  independence:"same_model_as_writer" 提示局限。

运行（对已有 business_report 补打分）：
    python -m eval.grader --report eval/reports/business_real_o01b/business_report.json \
        --mode real --max-cost 0.05 --out eval/reports/grader_o01b
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

from eval.business_eval import _case_source_texts, load_dataset
from src.application.request import TaskRequest
from src.harness.model_gateway import JobLedger, job_scope, model_call
from src.harness.structured import extract_json

DIMENSIONS = ("correctness", "structure", "citations", "completeness")
ACCEPT_MIN = 4          # 每维 ≥4 且无伪造/无未解析引用才算 accept
ANCHORS = {
    "correctness": "1=关键事实大量错误/编造 3=部分错误或过度引申 5=与资料一致、无未经证据支持断言",
    "structure": "1=无结构 3=章节混乱或缺失 5=章节齐全、段落围绕提纲目的",
    "citations": "1=大量引用不可定位 3=部分断言缺引用 5=每处断言都有可定位引用且引用恰当",
    "completeness": "1=缺核心内容 3=覆盖大部分要求 5=任务要求与必要事实全覆盖、缺口如实声明",
}
RUBRIC_PROMPT = (
    "你是独立审阅评分员，为一份研究报告按以下 1~5 评分（不得替作者辩护）："
    + "；".join(f"{k}：{v}" for k, v in ANCHORS.items()))


class GraderError(RuntimeError):
    """评分 Agent 输出不可用：保留现场，不产生分数。"""


def _grade_messages(task: dict, goal: str, source_texts: list,
                    final_report: str, evidence: list[dict],
                    machine_checks: dict) -> list[dict]:
    facts_block = "\n".join(
        f"- [关键事实#{i + 1}] {f.get('claim', '')}（来源 {f.get('source_id')}，"
        f"原文：“{f.get('quote', '')}”）"
        for i, f in enumerate(task.get("facts", [])))
    sources_block = "\n\n".join(
        f"--- 来源：{title} ---\n{text[:12000]}"
        for title, text in source_texts)
    evidence_block = "\n".join(
        f"- {e.get('evidence_id')} tag={e.get('tag')} {e.get('fact', '')}"
        f"（来源 {e.get('source_id')}，quote：“{e.get('quote', '')[:120]}”）"
        for e in (evidence or []))
    schema = ('{"dimensions":{"correctness":{"score":1,"rationale":"依据…",'
              '"cited_evidence_ids":["E-001"]},"structure":{...},"citations":{...},'
              '"completeness":{...}},"issues":["问题与位置"],'
              '"fabrication_flags":["发现的编造/无依据断言"],"overall_verdict":"accept|draft|fail"}')
    return [
        {"role": "system", "content": (
            RUBRIC_PROMPT +
            "。核对规则：只依据给定来源与证据；引用必须真实存在于证据列表与正文；"
            "发现编造来源/断言必须写入 fabrication_flags（任一存在即不得判 accept）；"
            "正文引用不可定位记为 citations 扣分项。禁止输出 JSON 之外的内容。"
            f"输出结构（紧凑单行，维度字段齐全）：{schema}")},
        {"role": "user", "content": (
            f"任务要求：{goal}\n必需章节：{task.get('sections')}\n"
            f"关键事实与禁止项：{facts_block}\n禁止项：{task.get('forbidden_claims')}\n"
            f"预期交付：{task.get('expected_outcome')}\n\n"
            f"允许的来源全文：\n{sources_block}\n\n证据列表：\n{evidence_block or '（无）'}\n"
            f"机器检查（仅参考，需复核）：{json.dumps(machine_checks, ensure_ascii=False)}\n\n"
            f"---- 报告全文开始 ----\n{final_report}\n---- 报告全文结束 ----")},
    ]


def _grade_call(llm, messages, *, workspace_root: Path, mode: str):
    """经统一网关调用；真实模式单独建 grader 账本记账，不混入被评任务账本。"""
    if getattr(llm, "run_mode", None) != "real":
        reply = llm.chat(messages)
        return reply, {"mode": mode, "model": getattr(llm, "model_name", "stub"),
                       "usage_complete": True, "estimated_cost_usd": 0.0}
    jobs = Path(workspace_root) / "grader_jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    request = TaskRequest("专职评测打分", mode="real", max_calls=8,
                          max_output_tokens=32768, max_seconds=600)
    ledger = JobLedger(jobs / ("job_g_" + uuid.uuid4().hex), request)
    with job_scope(ledger):
        try:
            reply = model_call(llm, messages, purpose="grader_eval", role="grader")
        finally:
            ledger.finish("completed")
    summary = ledger.summary()
    return reply, {"mode": "real", "model": getattr(llm, "model_name", "?"),
                   "call_count": summary["call_count"],
                   "estimated_cost_usd": summary["estimated_cost_usd"],
                   "unknown_usage_calls": summary["unknown_usage_calls"]}


def grade_report(*, task: dict, goal: str, source_texts: list,
                 final_report: str, evidence: list[dict],
                 machine_checks: dict, llm, workspace_root: Path,
                 mode: str = "mock", writer_model: str | None = None) -> dict:
    """对一个报告实例打分。返回完整评分记录（含程序重算与元信息）。"""
    data = None
    raw = ""
    for attempt in (1, 2):
        messages = _grade_messages(task, goal, source_texts, final_report,
                                   evidence, machine_checks)
        if attempt == 2:
            messages = messages[:1] + [{
                "role": "system",
                "content": "上一次输出无法解析。这次只输出一个紧凑、完整、合法的 JSON"
                           "对象（四维字段齐全、issue≤10、fabrication_flags≤5），"
                           "不要围栏与解释。"}] + messages[1:]
        reply, ledger_meta = _grade_call(llm, messages,
                                         workspace_root=workspace_root, mode=mode)
        raw = reply.content or ""
        data = extract_json(raw)
        if data is not None:
            break
    if not data:
        raise GraderError("评分 Agent 输出不是合法 JSON 对象"
                          + (f"；现场片段：{raw[:200]}" if raw else ""))
    return finalize_grading(data, machine_checks=machine_checks,
                            ledger_meta=ledger_meta,
                            independence=getattr(llm, "model_name", "stub"),
                            writer_model=writer_model)


def finalize_grading(data: dict, *, machine_checks: dict,
                     ledger_meta: dict, independence: str,
                     writer_model: str | None) -> dict:
    """程序交叉核验 + 规则重算（不信自报 verdict 盖章）。"""
    dims = {}
    problems = []
    evidence_ids = set()
    report_mentions = machine_checks.get("citation_tokens", 0)
    for name in DIMENSIONS:
        entry = data.get("dimensions", {}).get(name)
        if not isinstance(entry, dict):
            dims[name] = {"score": None, "rationale": "", "cited_evidence_ids": [],
                          "problem": "缺少维度"}
            continue
        try:
            score = int(entry.get("score"))
        except (TypeError, ValueError):
            score = None
        if score is None or not (1 <= score <= 5):
            dims[name] = {"score": None, "rationale": str(entry.get("rationale", ""))[:200],
                          "cited_evidence_ids": [], "problem": "分数越界或缺失"}
            continue
        dims[name] = {"score": score,
                      "rationale": str(entry.get("rationale", ""))[:200],
                      "cited_evidence_ids": entry.get("cited_evidence_ids") or []}
    fabrication = [str(x)[:200] for x in (data.get("fabrication_flags") or [])
                   if str(x).strip()]
    issues = [str(x)[:200] for x in (data.get("issues") or []) if str(x).strip()]
    claimed = sorted({iid for d in dims.values()
                      for iid in d.get("cited_evidence_ids", []) if str(iid).startswith("E-")})
    if claimed and machine_checks.get("citation_tokens", 0) is not None:
        report_ids = set(machine_checks.get("cited_ids", []))
        if report_ids:
            fake = sorted(set(claimed) - report_ids)
            if fake:
                problems.append(f"评分者引用了正文/证据中不存在的 id：{fake}")
                fabrication.append("引用证据 id 与正文不符（程序核验）")
    scores = [d["score"] for d in dims.values() if d.get("score")]
    verdict_raw = str(data.get("overall_verdict") or "").strip().lower()
    unresolved = int(machine_checks.get("unresolved_citations") or 0)
    has_fabrication = bool(fabrication)
    scores_present = len(scores) == len(DIMENSIONS)
    scores_ok = scores_present and all(s >= ACCEPT_MIN for s in scores)
    # 规则重算：伪造 → fail；维度缺失 → fail；引用未解析 → draft；低分 → draft
    if has_fabrication:
        computed = "fail"
    elif not scores_present:
        computed = "fail"
    elif scores_ok and unresolved == 0 and verdict_raw == "accept":
        computed = "accept"
    else:
        computed = "draft"
    return {
        "grader": "agent",
        "human_confirmed": False,
        "overall_verdict": verdict_raw,
        "computed_verdict": computed,
        "dimensions": dims,
        "issues": issues,
        "fabrication_flags": fabrication,
        "program_problems": problems,
        "meta": {"grader_model": independence,
                 "writer_model": writer_model,
                 "independence": ("different" if writer_model and writer_model != independence
                                  else "same_model_as_writer（建议用独立模型复核）"),
                 **ledger_meta,
                 "graded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "note": "初步自动评分；最终业务验收需人工确认（human_confirmed=false）"}}

def dimension_means(grades: list[dict]) -> dict:
    means = {}
    for name in DIMENSIONS:
        values = [g["dimensions"][name]["score"] for g in grades
                  if isinstance(g.get("dimensions", {}).get(name, {}).get("score"), int)]
        means[name] = round(sum(values) / len(values), 2) if values else None
    return means


def main() -> None:
    parser = argparse.ArgumentParser(description="专职评测 Agent（自动评分，需人工确认）")
    parser.add_argument("--report", required=True, help="business_report.json 路径")
    parser.add_argument("--out", default=None, help="输出目录（默认与报告同目录）")
    parser.add_argument("--mode", choices=("mock", "real"), default="mock")
    parser.add_argument("--max-cost", type=float, default=None,
                        help="真实模式单次评分账本费用阈值（建议 0.02~0.05）")
    parser.add_argument("--task", default=None, help="只评分指定案例 id")
    args = parser.parse_args()
    if args.mode == "real" and (args.max_cost is None or args.max_cost < 0):
        parser.error("真实评分必须给 --max-cost")
    report_path = Path(args.report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    dataset = load_dataset()
    by_id = {t["id"]: t for t in dataset["tasks"]}
    workspace_root = report_path.parent / "workspace"
    out_dir = Path(args.out) if args.out else report_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    from src.harness.models.factory import build_adapter
    from config.settings import Settings
    llm = build_adapter(None, Settings(), mode=args.mode)
    grades = []
    for record in report.get("records", []):
        if args.task and record["id"] != args.task:
            continue
        if record.get("status") not in ("passed", "failed"):
            continue
        task = by_id.get(record["id"])
        if task is None:
            continue
        try:
            job_dir = workspace_root / "jobs" / record.get("root_job_id", "")
            final_text = record.get("final_text_head", "") or ""
            if (job_dir / "job.json").exists():
                job_file = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
                final_text = job_file.get("final_text") or final_text
            evidence = []
            if (job_dir / "evidence.json").exists():
                evidence = json.loads((job_dir / "evidence.json").read_text(encoding="utf-8"))["items"]
            grade = grade_report(
                task=task, goal=task["request"],
                source_texts=_case_source_texts(dataset, task["source_ids"]),
                final_report=final_text, evidence=evidence,
                machine_checks=record.get("machine_checks", {}),
                llm=llm, workspace_root=workspace_root, mode=args.mode)
            grade["case_id"] = record["id"]
            grade["attempt"] = record.get("attempt")
            grades.append(grade)
            print(record["id"], grade["computed_verdict"],
                  {k: grade["dimensions"][k].get("score") for k in DIMENSIONS})
        except GraderError as e:
            print(record["id"], "GRADE_FAILED:", str(e)[:160])
    payload = {"meta": {"source_report": str(report_path), "mode": args.mode,
                        "graded": len(grades),
                        "accept": sum(1 for g in grades
                                      if g["computed_verdict"] == "accept"),
                        "dimension_means": dimension_means(grades),
                        "human_confirmed": False,
                        "note": "自动初步评分；最终业务验收需人工确认或显式策略放行"},
               "grades": grades}
    (out_dir / "grading_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"graded": payload["meta"]["graded"],
                      "accept": payload["meta"]["accept"],
                      "dimension_means": payload["meta"]["dimension_means"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
