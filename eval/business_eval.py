# -*- coding: utf-8 -*-
"""
eval/business_eval.py —— 业务评测运行器（S6-01~04/06）

原则（与验收基线一致）：
- 每个业务案例都走 ResearchApplication 统一入口（flow=research），不另写更简单的测试路径；
- 真实评测显式 --mode real --max-cost：缺 Key/认证失败时整批标 not_executed（记录原因），
  绝不用 Mock 成绩顶替，也不发一个模型请求；
- 报告记录代码/数据集版本、实际模型/工具、配置快照（脱敏）与模式；
- 20 业务任务（整理8/研究8/改稿4）默认每任务 3 次、10 故障至少 2 轮；
  每次失败样本（job.json/pipeline/evidence/ledger/报告产物）整目录保存；
- 完成/耗时/费用/失败原因逐次记录；"通过"只统计 accepted 且 dataset 硬条件满足的尝试；
  机器检查列只做辅助（引用可解析/章节覆盖/禁语命中提示），人工评分不自动盖章（见 human_scores）。
- 改稿（revision）案例需要"从原稿继续"的会话式改稿链（initial_draft 起步），
  当前链不支持 → 明确跳过并记 reason，不冒充通过。

运行：python -m eval.business_eval --mode mock | --mode real --max-cost 0.1 [--task o01]
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from eval.research_cases import load_dataset

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "eval" / "reports" / "business"


def _version_snapshot() -> dict:
    names = ("langgraph", "langchain-core", "openai", "pytest", "python-dotenv")
    packages = {}
    for name in names:
        try:
            packages[name] = importlib.metadata.version(name)
        except Exception:
            packages[name] = None
    head_file = PROJECT_ROOT / ".git" / "HEAD"
    revision = "no_git_commit_yet"
    try:
        if head_file.exists():
            ref = head_file.read_text(encoding="utf-8").strip()
            if ref.startswith("ref: "):
                ref_path = PROJECT_ROOT / ".git" / ref[5:]
                if ref_path.exists():
                    revision = ref_path.read_text(encoding="utf-8").strip()[:12]
    except Exception:
        pass
    return {"python": sys.version.split()[0], "packages": packages,
            "code_revision": revision}


def _config_snapshot(settings, mode: str, tools: list[str]) -> dict:
    """脱敏配置快照：不含 Key/接口地址明文。"""
    if settings is None:
        return {"mode": mode, "tools": tools,
                "note": "使用默认环境配置（未注入 settings 对象）"}
    return {"mode": mode,
            "model_provider": settings.model_provider,
            "model_name": settings.model_name,
            "tools": tools,
            "trace_level": settings.trace_level,
            "note": "不含 API Key 与接口地址；SEARCH_PROVIDER="
                    + (settings.search_provider or "(未配置)")}


def real_config_ready(*, settings, profile_name: str | None) -> tuple[bool, str]:
    """真实模式探针：不发请求。缺配置/认证失败 → (False, 原因)。"""
    from src.harness.models import factory
    try:
        llm = factory.build_adapter(profile_name, settings, mode="real")
        if getattr(llm, "run_mode", None) != "real":
            return False, "实际适配器不是 real（禁止静默回退）"
        return True, ""
    except Exception as e:  # noqa: BLE001 —— 探针只报类型与原因
        return False, f"{type(e).__name__}: {e}"


def _headings(text: str) -> set[str]:
    return {re.sub(r"^#+\s*", "", line).strip().lower()
            for line in (text or "").splitlines()
            if line.strip().startswith("#")}


def _case_source_texts(dataset: dict, source_ids: list[str]) -> list[str]:
    by_id = {s["id"]: s for s in dataset["sources"]}
    return [(by_id[sid]["title"], by_id[sid]["text"]) for sid in source_ids if sid in by_id]


def _machine_checks(task: dict, final_text: str, evidence_items: list[dict]) -> dict:
    """程序辅助检查（S6-05：只做事实性结构检查，不替代人工评分）。"""
    citations = re.findall(r"\[(E-\d{3})\]", final_text or "")
    evidence_ids = {item["evidence_id"] for item in evidence_items}
    unknown = sorted({c for c in citations if c not in evidence_ids})
    headings = _headings(final_text)
    section_hits = sum(1 for s in task.get("sections", [])
                       if s.strip().lower() in headings)
    forbidden = [c for c in task.get("forbidden_claims", [])
                 if c and c in (final_text or "")]
    fact_hits = 0
    for fact in task.get("facts", []):
        claim = fact.get("claim", "")
        if claim and claim in (final_text or ""):
            fact_hits += 1
    return {"citation_tokens": len(citations),
            "unresolved_citations": len(unknown),
            "required_sections": len(task.get("sections", [])),
            "section_hits": section_hits,
            "fact_hits": fact_hits,
            "fact_total": len(task.get("facts", [])),
            "forbidden_claim_hits": len(forbidden),
            "forbidden_claims": forbidden[:3],
            "note": "机器检查仅供参考；事实支持/语义正确需人工评分"}


def run_business_eval(*, workspace_root, mode: str = "mock", llm=None,
                      repeats: int = 3, fault_rounds: int = 2,
                      task_filter: str | None = None, max_cost: float | None = None,
                      out_dir: Path | None = None,
                      settings=None, profile_name: str | None = None,
                      grader_llm=None, grade: bool = False,
                      grader_model: str | None = None,
                      open_book: bool = False,
                      batch_max_cost: float | None = None) -> dict:
    if mode not in ("mock", "real"):
        raise ValueError("mode 必须为 mock 或 real")
    if repeats < 1:
        raise ValueError("repeats 必须 ≥1")
    if fault_rounds < 1:
        raise ValueError("fault_rounds 必须 ≥1")
    if mode == "real" and (isinstance(max_cost, bool) or not isinstance(max_cost, int | float)
                           or not math.isfinite(max_cost) or max_cost < 0):
        raise ValueError("真实评测必须设置非负有限 --max-cost（单任务美元估算停止阈值）")
    if batch_max_cost is not None and (isinstance(batch_max_cost, bool)
                                       or not isinstance(batch_max_cost, int | float)
                                       or not math.isfinite(batch_max_cost)
                                       or batch_max_cost < 0):
        raise ValueError("--batch-max-cost 必须为非负有限数（整批累计美元估算停止阈值）")
    dataset = load_dataset()
    meta = dataset["meta"]
    started = time.time()
    root = Path(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    records: list[dict] = []
    samples_dir = None
    if out_dir is not None:
        samples_dir = Path(out_dir) / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)

    from src.application.request import TaskRequest
    from src.application.research import ResearchApplication
    from src.harness.models import factory
    from src.harness.tools.registry import ToolRegistry

    # 专职评测 Agent 装配：显式 llm > grade/grader_model 自动构造（GRADER_MODEL_NAME > 主模型）
    active_grader = grader_llm
    grader_used_model = getattr(grader_llm, "model_name", None) if grader_llm else None
    if active_grader is None and (grade or grader_model):
        from eval.grader import build_grader_llm
        active_grader, grader_used_model = build_grader_llm(mode, settings=settings,
                                                            model=grader_model)

    ready, config_reason = (True, "") if mode == "mock" else \
        real_config_ready(settings=settings, profile_name=profile_name)
    tasks = [t for t in dataset["tasks"] if not task_filter or t["id"] == task_filter]
    if task_filter and not tasks:
        raise ValueError("指定的业务案例不存在")

    selected = []
    batch_spent = 0.0
    batch_stop = ""

    def cap_reached() -> bool:
        return batch_max_cost is not None and batch_spent >= batch_max_cost

    for task in tasks:
        if mode == "real" and not ready:
            for rep in range(repeats):
                results.append({"id": task["id"], "category": task["category"],
                                "attempt": rep + 1,
                                "status": "not_executed",
                                "reason": config_reason,
                                "note": "真实模式配置不可用：整批未执行，未用 Mock 顶替"})
            continue
        for rep in range(repeats):
            if batch_stop or cap_reached():
                results.append({"id": task["id"], "category": task["category"],
                                "attempt": rep + 1, "status": "not_executed",
                                "reason": batch_stop or
                                f"批次累计上限（batch_max_cost={batch_max_cost}）已用尽",
                                "note": "批次累计限额或未知用量：剩余尝试未执行，未用 Mock 顶替"})
                continue
            selected.append(task["id"])
            entry = {"id": task["id"], "category": task["category"],
                     "attempt": rep + 1, "task": task["request"]}
            attempt_started = time.time()
            try:
                sources = _case_source_texts(dataset, task["source_ids"])
                if not sources:
                    raise ValueError("案例来源文本缺失")
                from src.llm.mock import MockLLM
                if llm is None:
                    if mode == "mock":
                        llm = MockLLM()
                    else:
                        llm = factory.build_adapter(profile_name, settings, mode="real")
                request = TaskRequest(
                    task=task["request"], mode=mode, flow="research",
                    max_iterations=8, max_calls=60, max_output_tokens=200000,
                    max_seconds=1500,
                    max_cost=max_cost if mode == "real" else None,
                    texts=tuple(text for _, text in sources),
                    base_draft=task.get("initial_draft") or "",
                    system_extra="资料标题提示：" + "；".join(title for title, _ in sources),
                    # 必需章节是用户可见的任务要求，正常传入（S6-05 闸门）。
                    required_sections=tuple(task.get("sections") or ()),
                    # S8-B 关闭开卷：关键事实/禁止断言属于评分答案，默认留在评测端，
                    # 不注入写作提示词；open_book=True 仅作对照实验并在报告 meta 标记。
                    forbidden_claims=(tuple(task.get("forbidden_claims") or ())
                                      if open_book else ()),
                    key_facts=(tuple(f.get("claim", "") for f in task.get("facts") or ()
                                     if f.get("claim")) if open_book else ()))
                app = ResearchApplication(request, settings=settings,
                                          workspace_root=root, llm=llm)
                outcome = app.run()
                job_dir = root / "jobs" / outcome.root_job_id
                ledger = {}
                ledger_path = job_dir / "ledger.json"
                if ledger_path.exists():
                    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
                evidence = []
                evidence_path = job_dir / "evidence.json"
                if evidence_path.exists():
                    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))["items"]
                pipeline = outcome.as_dict() if hasattr(outcome, "as_dict") else {}
                checks = _machine_checks(task, outcome.final_text or "", evidence)
                grader_record = None
                if active_grader is not None:
                    # 专职评测 Agent：独立模型按评分表打分；失败不影响链结果
                    try:
                        from eval.grader import GraderError, grade_report
                        grader_record = grade_report(
                            task=task, goal=task["request"],
                            source_texts=_case_source_texts(dataset, task["source_ids"]),
                            final_report=outcome.final_text or "",
                            evidence=evidence, machine_checks=checks,
                            llm=active_grader, workspace_root=root,
                            mode=getattr(active_grader, "run_mode", mode) or mode,
                            writer_model=getattr(llm, "model_name", "?"))
                    except GraderError as e:
                        grader_record = {"grader": "agent", "human_confirmed": False,
                                         "error": str(e)[:200]}
                if grader_record is not None:
                    entry["grader"] = grader_record
                accepted = getattr(outcome, "draft_level", None) == "accepted"
                entry.update(
                    status="passed" if accepted else "failed",
                    draft_level=getattr(outcome, "draft_level", None),
                    termination_reason=outcome.termination_reason,
                    elapsed_seconds=round(time.time() - attempt_started, 3),
                    estimated_cost_usd=ledger.get("estimated_cost_usd"),
                    unknown_usage_calls=ledger.get("unknown_usage_calls", 0),
                    root_job_id=outcome.root_job_id,
                    message=(getattr(outcome, "message", "") or "")[:300],
                    machine_checks=checks,
                    chain_hard_checks=pipeline.get("hard_checks") or {},
                    revision_of=task.get("revision_of"),
                    changed=(outcome.final_text or "").strip()
                    != (task.get("initial_draft") or "").strip(),
                    final_text_head=(outcome.final_text or "")[:200])
                if mode == "real" and ledger.get("unknown_usage_calls"):
                    entry["note"] = "未知用量导致停止：结果不确定，不计通过"
                records.append(entry)
                # S6-04：失败样本整目录保存
                if entry["status"] != "passed" and samples_dir is not None:
                    target = samples_dir / f"{task['id']}_rep{rep + 1}"
                    target.mkdir(parents=True, exist_ok=True)
                    for name in ("job.json", "ledger.json", "pipeline.json",
                                 "evidence.json", "sources.json"):
                        source = job_dir / name
                        if source.exists():
                            (target / name).write_text(source.read_text(encoding="utf-8"),
                                                       encoding="utf-8")
                    artifacts_dir = job_dir / "artifacts"
                    if artifacts_dir.is_dir():
                        for artifact in artifacts_dir.glob("report.*.md"):
                            (target / artifact.name).write_bytes(artifact.read_bytes())
            except Exception as e:  # noqa: BLE001 —— 失败样本保留，不写异常正文
                entry.update(status="failed", error=type(e).__name__,
                             message=str(e)[:200],
                             elapsed_seconds=round(time.time() - attempt_started, 3))
                records.append(entry)
                if samples_dir is not None:
                    (samples_dir / f"{task['id']}_rep{rep + 1}").mkdir(exist_ok=True)
            results.append(entry)
            # O-09：批次累计限额（--max-cost 只是单任务上限，不能封顶整批花费）
            batch_spent += entry.get("estimated_cost_usd") or 0
            if batch_max_cost is not None and batch_spent >= batch_max_cost:
                batch_stop = (f"批次累计上限已达（batch_max_cost={batch_max_cost}，"
                              f"估算已用 {batch_spent:.4f}）")
            if mode == "real" and entry.get("unknown_usage_calls"):
                batch_stop = "未知用量：按纪律停止剩余真实任务（不得静默当作零成本）"

    # ---- 故障案例：至少 fault_rounds 轮（可自动化的探针列在此，其余记 manual）-----
    fault_rows: list[dict] = []
    automated = {"f01": _fault_invalid_config, "f07": _fault_empty_body}
    for fault in dataset["faults"]:
        if task_filter and fault["id"] != task_filter:
            continue
        probe = automated.get(fault["id"])
        for round_index in range(fault_rounds):
            if probe is None:
                fault_rows.append({"id": fault["id"], "round": round_index + 1,
                                   "status": "manual",
                                   "reason": "需要服务/人工场景注入（见验证说明），试用期执行"})
                continue
            try:
                ok, detail = probe(settings=settings)
                fault_rows.append({"id": fault["id"], "round": round_index + 1,
                                   "status": "passed" if ok else "failed",
                                   "detail": detail})
            except Exception as e:  # noqa: BLE001
                fault_rows.append({"id": fault["id"], "round": round_index + 1,
                                   "status": "failed",
                                   "detail": f"{type(e).__name__}: {e}"})

    executed = [r for r in records if r.get("status") == "passed"]
    grades = [r["grader"] for r in records
              if isinstance(r.get("grader"), dict)
              and isinstance(r["grader"].get("computed_verdict"), str)]
    grader_meta = {}
    if grades:
        from eval.grader import dimension_means
        grader_meta = {
            "graded": len(grades),
            "grader_accept": sum(1 for g in grades
                                 if g["computed_verdict"] == "accept"),
            "dimension_means": dimension_means(grades),
            "writer_model": getattr(llm, "model_name", None) if llm is not None else None,
            "grader_model": grader_used_model,
            "human_confirmed": False,
            "note": "专职评测 Agent 初步自动评分；最终业务验收需人工确认或显式策略放行"}
    report = {
        "meta": {
            "name": meta["name"], "dataset_version": meta.get("version"),
            "mode": mode, "real_config_ready": ready,
            "real_config_reason": config_reason if mode == "real" else "",
            "repeats": repeats, "fault_rounds": fault_rounds,
            "max_cost_usd": max_cost if mode == "real" else None,
            "batch_max_cost_usd": batch_max_cost,
            "batch_spent_usd": round(batch_spent, 6),
            "batch_stop_reason": batch_stop,
            "batch_cost_note": ("max_cost_usd 为单任务上限；batch_max_cost_usd 为整批累计上限"
                                "（O-09）；两者均为本地保守估算，非账单"),
            "open_book": bool(open_book),
            "open_book_note": ("对照实验：关键事实/禁止断言已注入写作端（开卷），"
                               "结果不可与闭卷批次合并比较" if open_book
                               else "闭卷：关键事实/禁止断言留在评测端（S8-B 默认）"),
            "versions": _version_snapshot(),
            "config": _config_snapshot(settings, mode,
                                       sorted(t.name for t in
                                              ToolRegistry.with_builtins().list())),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "scope": "business_acceptance_pending_human_scoring"},
        "totals": {
            "attempts_total": len(records),
            "passed": len(executed),
            "failed": len(records) - len(executed),
            "skipped_or_not_executed": sum(1 for r in results
                                           if r.get("status") in ("skipped",
                                                                  "not_executed")),
            "revision_skipped": sum(1 for r in results
                                    if r.get("reason") == "revision_flow_not_ready"),
            "fault_rounds_executed": len([r for r in fault_rows
                                          if r["status"] in ("passed", "failed")]),
            "note": "passed 只计 accepted 尝试；草稿/失败均为 failed；"
                    "人工评分通过后才会更新业务通过率"},
        "elapsed_seconds": round(time.time() - started, 2),
        "results": results,
        "records": records,
        "fault_rows": fault_rows,
        "samples_dir": str(samples_dir) if samples_dir else None,
        "grader": grader_meta,
    }
    return report


def _fault_invalid_config(settings=None) -> tuple[bool, str]:
    """f01：MODEL_API_KEY 为空时真实模式必须在启动前拒绝，零模型请求。"""
    import os
    from src.harness.models import factory
    old = os.environ.get("MODEL_API_KEY", "")
    os.environ["MODEL_API_KEY"] = ""
    try:
        ok, reason = real_config_ready(settings=settings, profile_name=None)
        return (not ok and "MODEL_API_KEY" in reason), reason
    finally:
        if old:
            os.environ["MODEL_API_KEY"] = old
        else:
            os.environ.pop("MODEL_API_KEY", None)


def _fault_empty_body(settings=None) -> tuple[bool, str]:
    """f07：空正文来源被明确分类 empty，不会进入证据。"""
    from pathlib import Path
    import tempfile
    from src.harness.storage.sources import SourceStore
    with tempfile.TemporaryDirectory() as tmp:
        store = SourceStore(Path(tmp) / "job")
        record = store.add_paste("   \n", display_index=1)
        return record.status == "empty", f"分类={record.status}"


def render_markdown(report: dict) -> str:
    m = report["meta"]
    t = report["totals"]
    lines = [
        f"# 业务评测：{m['name']}",
        f"- 生成时间：{m['generated_at']}｜模式：{m['mode']}｜数据集版本：{m['dataset_version']}",
        f"- 真实配置可用：{m['real_config_ready']}{'（' + m['real_config_reason'] + '）' if m['real_config_reason'] else ''}",
        f"- 重复次数：{m['repeats']}｜故障轮数：{m['fault_rounds']}｜单任务上限：{m['max_cost_usd']} 美元｜"
        f"整批累计上限：{m.get('batch_max_cost_usd')} 美元（已用估算 {m.get('batch_spent_usd')}）",
        f"- 批次停止原因：{m.get('batch_stop_reason') or '（未触发）'}",
        f"- 代码版本：{m['versions']['code_revision']}｜Python {m['versions']['python']}｜"
        f"包：{json.dumps(m['versions']['packages'], ensure_ascii=False)}",
        f"- 模型/工具快照：{json.dumps(m['config'], ensure_ascii=False)}", "",
        "## 总量",
        f"- 尝试 {t['attempts_total']}：passed {t['passed']} / failed {t['failed']}"
        f" / 跳过或未执行 {t['skipped_or_not_executed']}（其中改稿未接通 {t['revision_skipped']}）",
        f"- 故障轮已执行：{t['fault_rounds_executed']}",
        f"- 用时：{report['elapsed_seconds']}s",
        f"- 说明：{t['note']}", "",
        "## 逐次记录",
    ]
    for r in report["records"]:
        checks = r.get("machine_checks", {})
        lines.append(
            f"- {r['id']}#{r.get('attempt')} [{r['status']}] "
            f"分级={r.get('draft_level')} 原因={r.get('termination_reason')} "
            f"耗时={r.get('elapsed_seconds')}s 费用={r.get('estimated_cost_usd')} "
            f"未知用量={r.get('unknown_usage_calls', 0)} "
            f"引用={checks.get('citation_tokens')}/未解析={checks.get('unresolved_citations')} "
            f"章节={checks.get('section_hits')}/{checks.get('required_sections')} "
            f"事实命中={checks.get('fact_hits')}/{checks.get('fact_total')} "
            f"禁语命中={checks.get('forbidden_claim_hits')}")
    lines.append("")
    for r in report["results"]:
        if r.get("status") in ("skipped", "not_executed"):
            lines.append(f"- {r['id']} ⏭ {r.get('status')}：{r.get('reason', r.get('note', ''))}")
    for f in report["fault_rows"]:
        lines.append(f"- 故障 {f['id']} 第{f['round']}轮 [{f['status']}] "
                     f"{f.get('detail', f.get('reason', ''))}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="业务评测运行器（S6）")
    parser.add_argument("--mode", choices=("mock", "real"), default="mock")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--fault-rounds", type=int, default=2)
    parser.add_argument("--task", default=None)
    parser.add_argument("--max-cost", type=float, help="真实模式单任务美元估算停止阈值（必填）")
    parser.add_argument("--batch-max-cost", type=float, default=None,
                        help="真实模式整批累计美元估算上限（O-09）：达到后剩余尝试记 not_executed，"
                             "不静默继续花费")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--grade", action="store_true",
                        help="每个执行过的尝试用专职评测 Agent 自动打分（初步，需人工确认）")
    parser.add_argument("--grader-model", default=None,
                        help="评测者模型名（默认 GRADER_MODEL_NAME，再默认与主模型相同）")
    parser.add_argument("--open-book", action="store_true",
                        help="对照开关：把数据集关键事实/禁止断言注入写作端（开卷）；"
                             "默认闭卷（S8-B），开卷批次在 meta 标记、不可与闭卷合并比较")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        report = run_business_eval(workspace_root=out / "workspace", mode=args.mode,
                                   repeats=args.repeats, fault_rounds=args.fault_rounds,
                                   task_filter=args.task, max_cost=args.max_cost,
                                   out_dir=out, grade=args.grade,
                                   grader_model=args.grader_model,
                                   open_book=args.open_book,
                                   batch_max_cost=args.batch_max_cost)
    except ValueError as e:
        parser.error(str(e))
    (out / "business_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "business_report.md").write_text(
        render_markdown(report), encoding="utf-8")
    t = report["totals"]
    print(f"attempts={t['attempts_total']} passed={t['passed']} failed={t['failed']} "
          f"skipped/not_executed={t['skipped_or_not_executed']} "
          f"| 报告：{out / 'business_report.json'}")


if __name__ == "__main__":
    main()
