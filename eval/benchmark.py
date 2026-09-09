# -*- coding: utf-8 -*-
"""
eval/benchmark.py —— Benchmark 运行器（DEV_PLAN B2/B5/B7 / Milestone 2 步骤 31-36）

流程：显式选择 mock/real → 检查配置与能力 → AgentRuntime → 冒烟评测。
Mock 报告输出：
    eval/reports/benchmark_report.json
    eval/reports/benchmark_report.md
真实报告另存 benchmark_real_report；仅比较同模式、模型和任务集合的历史报告。

运行：python -m eval.benchmark            （默认 Mock 大脑）
      python -m eval.benchmark --task b01
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
DATASET_PATH = EVAL_DIR / "datasets" / "benchmark_v1.json"
REPORT_DIR = EVAL_DIR / "reports"
PROJECT_ROOT = EVAL_DIR.parent


def load_dataset() -> dict:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _read_trace(run_dir: str) -> list[dict]:
    lines = (Path(run_dir) / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines if x.strip()]


def run_benchmark(workspace_root: Path, run_only_safe: bool = True,
                  task_filter: str | None = None, *, mode: str = "mock",
                  settings=None, profile_name=None, max_cost: float | None = None) -> dict:
    """执行评测，返回报告 dict（不落盘）。workspace_root 由调用方管理。"""
    from eval.evaluators import e2e as e2e_eval
    from eval.evaluators import tool_metrics, trajectory
    from src.application.request import TaskRequest
    from src.application.research import ResearchApplication
    from src.harness.models.factory import build_adapter
    from src.harness.tools.registry import ToolRegistry

    if mode not in ("mock", "real"):
        raise ValueError("mode 必须为 mock 或 real")
    if mode == "mock" and not run_only_safe:
        raise ValueError("--no-skip 必须同时使用 --mode real；不能用 Mock 冒充真实评测")
    if mode == "real" and (isinstance(max_cost, bool)
            or not isinstance(max_cost, (int, float)) or not math.isfinite(max_cost) or max_cost < 0):
        raise ValueError("真实评测必须设置非负有限 --max-cost（美元估算停止阈值）")

    dataset = load_dataset()
    if task_filter and not any(t["id"] == task_filter for t in dataset["tasks"]):
        raise ValueError("指定的评测任务不存在")
    llm = build_adapter(profile_name, settings, mode=mode)
    if getattr(llm, "run_mode", None) != mode:
        raise ValueError("实际适配器与评测模式不一致，已停止")
    available = {t.name for t in ToolRegistry.with_builtins().list()}
    spent, budget_unknown = 0.0, False
    results = []
    for task in dataset["tasks"]:
        if task_filter and task["id"] != task_filter:
            continue
        entry = {"id": task["id"], "category": task["category"], "task": task["task"]}
        skip_reason = None
        if mode == "real" and task.get("mock_only"):
            skip_reason = "mock_only_expectation"
        elif set(task["expect"].get("tool", [])) - available:
            skip_reason = "capability_missing"
        elif task["mock_safe"] is False and run_only_safe:
            skip_reason = "outside_smoke_subset"
        elif mode == "real" and (budget_unknown or spent >= max_cost):
            skip_reason = "budget_unknown" if budget_unknown else "budget_exhausted"
        if skip_reason:
            entry["status"] = "skipped"
            entry["reason"] = skip_reason
            results.append(entry)
            continue
        holder = {}
        try:
            outcome = ResearchApplication(TaskRequest(
                task["task"], mode=mode, max_iterations=5,
                max_cost=max(0, max_cost-spent) if mode == "real" else None),
                llm=llm, settings=settings, workspace_root=workspace_root).run(
                on_event=lambda ev: holder.update(run_id=ev["run_id"]) if ev["type"] == "run_start" else None)
        except Exception as e:
            # 留下失败行，不把 SDK 响应/异常正文写入报告；用量不确定时停止批次。
            entry.update(status="failed", error=type(e).__name__, run_id=holder.get("run_id"))
            results.append(entry)
            budget_unknown = mode == "real"
            continue
        entry["run_id"] = outcome.run_id
        entry["root_job_id"] = outcome.root_job_id
        root_usage = json.loads((Path(workspace_root) / "jobs" / outcome.root_job_id / "ledger.json").read_text(encoding="utf-8"))
        # 批次累计根任务账本，包含辅助调用；不累加子run以免重复记账。
        cost = root_usage["estimated_cost_usd"]
        if mode == "real":
            budget_unknown = bool(root_usage["unknown_usage_calls"]) or cost is None
            if not budget_unknown:
                spent += cost
        events = _read_trace(outcome.workspace_dir)
        entry["outcome"] = {
            "final_text": outcome.final_text,
            "termination_reason": outcome.termination_reason,
            "iterations": outcome.iterations,
            "messages": outcome.messages,
        }
        entry["tool"] = tool_metrics.evaluate(events, task["expect"])
        entry["trajectory"] = trajectory.analyze(events, outcome)
        entry["e2e"] = e2e_eval.evaluate(task["expect"], outcome.final_text,
                                         entry["tool"], outcome)
        entry["status"] = "passed" if (entry["e2e"]["success"] and
            outcome.termination_reason == task["expect"].get("termination_reason", "success")) else "failed"
        results.append(entry)

    report = aggregate(dataset, results)
    report["meta"].update(mode=mode, brain=llm.model_name, provider=llm.provider,
                          selected_ids=[r["id"] for r in results],
                          max_cost_usd=max_cost if mode == "real" else None,
                          estimated_cost_usd=None if budget_unknown else spent,
                          scope="runtime_smoke_not_business_acceptance")
    return report


def aggregate(dataset: dict, results: list[dict]) -> dict:
    executed = [r for r in results if r["status"] in ("passed", "failed")]
    passed = [r for r in executed if r["status"] == "passed"]
    skipped = [r for r in results if r["status"] == "skipped"]

    by_cat: dict[str, dict] = {}
    for cat in dataset["meta"]["categories"]:
        cat_rows = [r for r in executed if r["category"] == cat]
        if cat_rows:
            by_cat[cat] = {"executed": len(cat_rows),
                           "passed": sum(1 for r in cat_rows if r["status"] == "passed")}

    report = {
        "meta": {"name": dataset["meta"]["name"], "dataset_tasks": len(dataset["tasks"]),
                 "generated_at": datetime.now().isoformat(timespec="seconds"),
                 "brain": "mock-rule-v1", "mode": "mock"},
        "totals": {
            "executed": len(executed), "passed": len(passed),
            "failed": len(executed) - len(passed), "skipped": len(skipped),
            "success_rate": (len(passed) / len(executed)) if executed else None,
            "tool_selection_accuracy": (
                sum(1 for r in executed if r.get("tool", {}).get("selection_ok")) / len(executed)
                if executed else None),
            "tool_argument_accuracy": (
                sum(1 for r in executed if r.get("tool", {}).get("argument_ok")) / len(executed)
                if executed else None),
            "avg_llm_calls": (sum(r["trajectory"]["llm_calls"] for r in executed)
                              / len(executed)) if executed and all("trajectory" in r for r in executed) else None,
        },
        "by_category": by_cat,
        "results": results,
    }
    return report


def _load_previous(mode="mock") -> dict | None:
    path = REPORT_DIR / ("benchmark_real_report.json" if mode == "real" else "benchmark_report.json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def render_markdown(report: dict, previous: dict | None) -> str:
    lines = [f"# Benchmark Report：{report['meta']['name']}",
             f"- 生成时间：{report['meta']['generated_at']}",
             f"- 模式：{report['meta']['mode']}（运行器冒烟测试，不代表研究写作业务验收）",
             f"- 数据集任务数：{report['meta']['dataset_tasks']}｜大脑：{report['meta']['brain']}", ""]
    t = report["totals"]
    lines.append(f"## 总体（executed={t['executed']}）")
    lines.append(f"- ✅ 通过 {t['passed']} ｜ ❌ 失败 {t['failed']} ｜ ⏭ 跳过 {t['skipped']}")
    lines.append(f"- 成功率：{fmt_pct(t['success_rate'])}")
    lines.append(f"- Tool Selection 准确率：{fmt_pct(t['tool_selection_accuracy'])}")
    lines.append(f"- Tool Argument 准确率：{fmt_pct(t['tool_argument_accuracy'])}")
    lines.append(f"- 平均 LLM 调用次数：{t['avg_llm_calls']}")

    if previous and previous.get("totals") and all(
            previous.get("meta", {}).get(k) == report["meta"].get(k)
            for k in ("name", "mode", "brain", "provider", "selected_ids")):
        p = previous["totals"]
        if p.get("success_rate") is not None and t["success_rate"] is not None:
            delta = t["success_rate"] - p["success_rate"]
            lines.append(f"- vs 上一版成功率：{fmt_pct(delta, signed=True)}")
    lines.append("")
    lines.append("## 按类别")
    lines.append("| 类别 | 执行 | 通过 |")
    lines.append("|---|---|---|")
    for cat, v in report["by_category"].items():
        lines.append(f"| {cat} | {v['executed']} | {v['passed']} |")
    lines.append("")
    lines.append("## 逐任务")
    lines.append("| id | 类别 | 状态 | 终止原因 | 工具 | 重复峰值 |")
    lines.append("|---|---|---|---|---|---|")
    for r in report["results"]:
        if r["status"] == "skipped":
            lines.append(f"| {r['id']} | {r['category']} | ⏭ {r['reason']} | - | - | - |")
        elif "error" in r:
            lines.append(f"| {r['id']} | {r['category']} | ❌ {r['error']} | unrecoverable_error | - | - |")
        else:
            lines.append(
                f"| {r['id']} | {r['category']} | "
                f"{'✅' if r['status'] == 'passed' else '❌'} | "
                f"{r['outcome']['termination_reason']} | "
                f"{','.join(r['tool']['used_tools']) or '—'} | "
                f"{r['trajectory']['duplicate_call_peak']} |")
    return "\n".join(lines) + "\n"


def fmt_pct(value, signed: bool = False) -> str:
    if value is None:
        return "—"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Benchmark（步骤 31-36）")
    parser.add_argument("--task", default=None, help="只跑指定任务 id")
    parser.add_argument("--mode", choices=("mock", "real"), default="mock")
    parser.add_argument("--profile", default=None, help="不填时使用项目 MODEL_NAME 等基础配置")
    parser.add_argument("--max-cost", type=float, help="真实评测批次的美元估算停止阈值，非账单硬封顶")
    parser.add_argument("--no-skip", action="store_true",
                        help="不跳过 real_model_only 任务（需要真实模型）")
    args = parser.parse_args()
    if args.no_skip and args.mode != "real":
        parser.error("--no-skip 需要 --mode real 和 --max-cost；不会隐式切换为付费模式")

    report_dir = Path(os.environ.get("EVAL_WORKSPACE", PROJECT_ROOT / "workspaces"))
    root = report_dir / f"eval_bench_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    root.mkdir(parents=True, exist_ok=True)

    previous = _load_previous(args.mode)
    try:
        report = run_benchmark(root, run_only_safe=not args.no_skip, task_filter=args.task,
                               mode=args.mode, profile_name=args.profile, max_cost=args.max_cost)
    except ValueError as e:
        parser.error(str(e))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = "benchmark_real_report" if args.mode == "real" else "benchmark_report"
    (REPORT_DIR / f"{stem}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / f"{stem}.md").write_text(
        render_markdown(report, previous), encoding="utf-8")

    t = report["totals"]
    print(f"executed={t['executed']} passed={t['passed']} failed={t['failed']} "
          f"skipped={t['skipped']} | success={fmt_pct(t['success_rate'])}")
    print(f"报告：{REPORT_DIR}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
