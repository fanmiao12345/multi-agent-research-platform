# -*- coding: utf-8 -*-
"""
orchestration/dynamic_team.py —— Dynamic Team 策略（步骤 82）

Manager(Planner) 动态决定：要不要多 Agent、需要哪些角色、哪些任务并行、
是否要 Reviewer。执行：依赖无冲突的任务按批次并行（Fan-out），逐层推进；
失败单次重规划。最终 = 按计划顺序的可追溯汇总。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from src.harness.model_gateway import BudgetStop

from src.harness.planning.executor import PlanResult, execute_plan
from src.harness.planning.planner import plan_task
from src.harness.planning.task import COMPLETED, FAILED, PENDING
from src.harness.planning.task_graph import TaskGraph
from src.harness.planning.replanner import replan
from src.orchestration.base import StrategyResult, Worker


def run_dynamic_team(task: str, worker: Worker, llm,
                     max_parallel: int = 3, name: str = "dynamic_team",
                     *, event_bus=None) -> StrategyResult:
    plan = plan_task(llm, task)          # Manager 决定组队（角色从 preferred_agent 来）
    graph = TaskGraph(plan)
    calls = {"n": 0}
    results: dict[str, str] = {}

    def execute_one(ptask):
        calls["n"] += 1
        if event_bus is not None:
            event_bus.publish("delegate", source=name, role=ptask.preferred_agent,
                              task_id=ptask.id, description=ptask.description[:80])
        return worker(ptask.description, ptask.preferred_agent)

    guard = 0
    limit = len(plan.tasks) * 4 + 4
    while not graph.all_completed() and guard < limit:
        guard += 1
        ready = graph.ready_tasks()
        if not ready:
            # 没有就绪任务 → 有 FAILED 且未重试过 → 重规划一次
            failed = [t for t in plan.tasks if t.status == FAILED]
            eligible = [t for t in failed if t.attempts < 1]
            if eligible:
                plan = replan(llm, plan, eligible)
                graph = TaskGraph(plan)
                continue
            break
        with ThreadPoolExecutor(max_workers=min(max_parallel, len(ready))) as pool:
            futures = {pool.submit(copy_context().run, execute_one, t): t for t in ready}
            for fut, t in futures.items():
                try:
                    results[t.id] = fut.result()
                    t.status = COMPLETED
                except BudgetStop:
                    raise
                except Exception as e:  # noqa: BLE001
                    t.status = FAILED
                    t.error = str(e)
    # 未完成的任务补一次失败标记
    for t in plan.tasks:
        if t.status == PENDING:
            t.status = FAILED
            t.error = "依赖失败未执行"

    summary = "\n".join(f"- [{t.id}]{t.status} {t.description[:50]}"
                        for t in plan.tasks)
    completed = [t for t in plan.tasks if t.status == COMPLETED]
    final = f"【Dynamic Team 汇总】完成 {len(completed)}/{len(plan.tasks)}\n{summary}"
    if completed:
        final += "\n\n主要产出：\n" + "\n".join(results.get(t.id, "")[:150]
                                                for t in completed)
    return StrategyResult(name=name, final=final, worker_calls=calls["n"],
                          stages=[{"id": t.id, "status": t.status}
                                  for t in plan.tasks])
