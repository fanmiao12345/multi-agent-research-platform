# -*- coding: utf-8 -*-
"""
orchestration/fanout.py —— Fan-out / Fan-in 策略（步骤 77）

把一个大任务拆成多个独立子任务并行执行（Worker 池并发上限可配），
随后统一合并（Fan-in）并做一份汇总。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from src.orchestration.base import StrategyResult, Worker, pack_card


def run_fanout(task: str, worker: Worker, subtasks: list[str],
               max_parallel: int = 3, role: str = "researcher",
               name: str = "fanout") -> StrategyResult:
    """subtasks：子任务描述列表（由调用方/Planner 提供）。"""
    if not subtasks:
        subtasks = [task]

    def one(sub: str) -> str:
        return worker(pack_card(task, f"子任务·{role}", None) + sub, role)

    outs: list[str] = [""] * len(subtasks)
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(subtasks))) as pool:
        futures = [pool.submit(copy_context().run, one, sub) for sub in subtasks]
        for i, fut in enumerate(futures):
            outs[i] = fut.result()

    combined = "\n\n".join(f"### 子任务 {i + 1} 产出\n{out}" for i, out in enumerate(outs))
    return StrategyResult(name=name, final=combined, worker_calls=len(subtasks),
                          stages=[{"subtask": s, "ok": bool(o)}
                                  for s, o in zip(subtasks, outs)])
