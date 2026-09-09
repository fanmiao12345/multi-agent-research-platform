# -*- coding: utf-8 -*-
"""
eval/benchmark_model.py —— Model/Budget 策略对比（I 阶段验收 / 步骤 97-103）

同一任务分别以 low_budget / balanced / high_quality 三档策略跑（Mock 大脑下
模型档案名仍如实记录），比较：调用数 / token / 估算成本 / 降级动作。

运行：python -m eval.benchmark_model
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from src.harness.budget_control import BudgetManager, degrade_plan
from src.harness.models.router import choose_model, classify_complexity

EVAL_DIR = Path(__file__).resolve().parent
CASES_PATH = EVAL_DIR / "datasets" / "model_strategy_cases_v1.json"
REPORT_DIR = EVAL_DIR / "reports"

BUDGET_MODES = ("low_budget", "balanced", "high_quality")


def run_strategy(task: str, mode: str, needs_tools: bool) -> dict:
    """按档位选择模型档案 + 预算；跑单智能体并记录结果（Mock 大脑）。"""
    from src.harness.models.factory import build_adapter
    from src.harness.runtime.agent_runtime import AgentRuntime
    from src.harness.runtime.run_context import RuntimeContext

    complexity = classify_complexity(task, needs_tools)
    profile_name = choose_model(complexity, mode, needs_tools)
    llm = build_adapter(profile_name, force_mock=True)
    budget = BudgetManager(max_cost_usd=0.5 if mode != "low_budget" else 0.05,
                           max_iterations=8)
    ctx = RuntimeContext.from_settings(max_iterations=8)
    outcome = AgentRuntime(llm).run_task(task, context=ctx)
    budget.state.iterations = outcome.iterations
    actions = degrade_plan(budget) if mode == "low_budget" else ["无需降级"]
    return {"mode": mode, "model_profile": profile_name,
            "complexity": complexity,
            "final_len": len(outcome.final_text),
            "termination_reason": outcome.termination_reason,
            "iterations": outcome.iterations,
            "degrade_actions": actions}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "rows": []}
    for case in cases:
        for mode in BUDGET_MODES:
            report["rows"].append(
                {"case": case["id"], **run_strategy(case["task"], mode,
                                                    case.get("needs_tools", False))})
    if not args.no_report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "model_strategy_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# Model Strategy Benchmark", f"- 时间：{report['generated_at']}", "",
          "| mode | profile | complexity | iterations | final_len | actions |",
          "|---|---|---|---|---|---|"]
    for r in report["rows"]:
        md.append(f"| {r['mode']} | {r['model_profile']} | {r['complexity']} | "
                  f"{r['iterations']} | {r['final_len']} | {';'.join(r['degrade_actions'])} |")
    text = "\n".join(md) + "\n"
    if not args.no_report:
        (REPORT_DIR / "model_strategy_report.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
