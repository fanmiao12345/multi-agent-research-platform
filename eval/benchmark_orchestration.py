# -*- coding: utf-8 -*-
"""
eval/benchmark_orchestration.py —— Orchestration Benchmark（DEV_PLAN G10 / 步骤 83）

同一任务集对比多种策略：Single Agent / Pipeline / Manager–Worker /
Fan-out / Dynamic Team，输出报告（worker 调用数、阶段数、产出长度），
为“哪些任务适合 Multi-Agent、哪个模式更省”提供第一版读数。

运行：python -m eval.benchmark_orchestration（默认 Mock 大脑，全离线）
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from src.orchestration.base import make_worker

EVAL_DIR = Path(__file__).resolve().parent
CASES_PATH = EVAL_DIR / "datasets" / "orchestration_cases_v1.json"
REPORT_DIR = EVAL_DIR / "reports"


def strategies() -> dict:
    """策略注册表：strategy(task, llm, worker) -> StrategyResult"""
    from src.orchestration.debate import run_debate
    from src.orchestration.dynamic_team import run_dynamic_team
    from src.orchestration.fanout import run_fanout
    from src.orchestration.manager_worker import run_manager_worker
    from src.orchestration.pipeline import run_pipeline

    def single(task, llm, worker):
        return worker(task, "agent")

    def fanout(task, llm, worker):
        # Fan-out 需要子任务清单：这里用规划器给出（查/整/写 三段）
        from src.harness.planning.planner import plan_task
        plan = plan_task(llm, task)
        subs = [t.description for t in plan.tasks]
        from src.orchestration.base import StrategyResult
        result = __import__("src.orchestration.fanout", fromlist=["run_fanout"]) \
            .run_fanout(task, worker, subs, role="worker")
        result.name = "fanout"
        return result

    def debate(task, llm, worker):
        return run_debate(task, worker, llm)

    return {
        "single": single,
        "pipeline": lambda t, llm, w: run_pipeline(t, w),
        "manager_worker": lambda t, llm, w: run_manager_worker(t, w, llm),
        "fanout": fanout,
        "dynamic_team": lambda t, llm, w: run_dynamic_team(t, w, llm),
        "debate": debate,
    }


def run_benchmark(llm=None, cases: list[dict] | None = None) -> dict:
    from src.harness.runtime.agent_runtime import AgentRuntime
    from src.llm.mock import MockLLM

    llm = llm or MockLLM()
    cases = cases or json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    worker = make_worker(AgentRuntime(llm))
    registry = strategies()
    rows = []
    for case in cases:
        for sname, fn in registry.items():
            try:
                result = fn(case["task"], llm, worker)
                final = result.final if hasattr(result, "final") else str(result)
                calls = getattr(result, "worker_calls", 1)
                rows.append({"case": case["id"], "strategy": sname,
                             "ok": bool(final and final.strip()),
                             "worker_calls": calls,
                             "final_len": len(final)})
            except Exception as e:  # noqa: BLE001
                rows.append({"case": case["id"], "strategy": sname, "ok": False,
                             "worker_calls": -1, "final_len": 0, "error": str(e)[:80]})

    by_strategy = {}
    for r in rows:
        s = by_strategy.setdefault(r["strategy"], {"runs": 0, "ok": 0,
                                                   "worker_calls": 0, "final_len": 0})
        s["runs"] += 1
        s["ok"] += int(r["ok"])
        s["worker_calls"] += r["worker_calls"]
        s["final_len"] += r["final_len"]
    return {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "by_strategy": by_strategy, "rows": rows}


def render_md(report: dict) -> str:
    lines = ["# Orchestration Benchmark", f"- 时间：{report['generated_at']}", "",
             "| 策略 | runs | 成功 | 平均 worker 调用 | 平均产出长度 |",
             "|---|---|---|---|---|"]
    for s, v in sorted(report["by_strategy"].items()):
        lines.append(f"| {s} | {v['runs']} | {v['ok']} | "
                     f"{v['worker_calls'] / v['runs']:.1f} | "
                     f"{v['final_len'] / v['runs']:.0f} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()
    report = run_benchmark()
    if not args.no_report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "orchestration_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (REPORT_DIR / "orchestration_report.md").write_text(
            render_md(report), encoding="utf-8")
    print(render_md(report))


if __name__ == "__main__":
    main()
