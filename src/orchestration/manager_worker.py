# -*- coding: utf-8 -*-
"""
orchestration/manager_worker.py —— Manager–Worker 策略（步骤 76 变体/78）

Manager = Planner（复用 planning）拆任务；Worker 按 preferred_agent 接单；
失败 → Replanner 差异式补救。最终产出 = 全部完成任务的可追溯汇总。
"""

from __future__ import annotations

from src.harness.planning.executor import PlanResult, plan_and_execute
from src.harness.planning.task import COMPLETED
from src.orchestration.base import StrategyResult, Worker


def run_manager_worker(task: str, worker: Worker, llm,
                       name: str = "manager_worker") -> StrategyResult:
    calls = {"n": 0}

    def runner(ptask):
        calls["n"] += 1
        return worker(ptask.description, ptask.preferred_agent)

    result: PlanResult = plan_and_execute(llm, task, runner=runner, max_replans=1)
    completed = [t for t in result.plan.tasks if t.status == COMPLETED]
    summary = "\n".join(f"- [{t.id}] {t.description[:60]} → {t.result[:80]}"
                        for t in completed)
    final = (f"【Manager 汇总】完成 {len(completed)}/{len(result.plan.tasks)} 个子任务"
             f"（重规划 {result.replans} 次）\n{summary}")
    return StrategyResult(name=name, final=final, worker_calls=calls["n"],
                          stages=[{"status": t.status, "id": t.id}
                                  for t in result.plan.tasks])
