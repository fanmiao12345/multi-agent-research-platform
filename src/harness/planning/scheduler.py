# -*- coding: utf-8 -*-
"""
harness/planning/scheduler.py —— Scheduler（DEV_PLAN C4 / 步骤 40）

根据当前 TaskGraph 状态计算 ready/blocked/running/finished 集合。
Scheduler 只做确定性逻辑 —— 不把调度决策交给 LLM。
"""

from __future__ import annotations

from src.harness.planning.task import (COMPLETED, FAILED, PENDING, RUNNING,
                                       SKIPPED)
from src.harness.planning.task_graph import TaskGraph


class Scheduler:
    def __init__(self, graph: TaskGraph):
        self.graph = graph

    def snapshot(self) -> dict:
        counts = {s: 0 for s in (PENDING, RUNNING, COMPLETED, FAILED, SKIPPED)}
        for t in self.graph.plan.tasks:
            counts[t.status] = counts.get(t.status, 0) + 1
        return {"ready": [t.id for t in self.next_ready()],
                "blocked": [t.id for t in self.graph.blocked_tasks()],
                "running": [t.id for t in self.graph.plan.tasks
                            if t.status == RUNNING],
                "finished": [t.id for t in self.graph.plan.tasks
                             if t.status in (COMPLETED, FAILED, SKIPPED)],
                "counts": counts}

    def next_ready(self) -> list:
        """下一个可执行批次（当前实现按依赖就绪逐个执行；并行交给上层并行度）。"""
        return self.graph.ready_tasks()

    def next_one(self):
        """取一个就绪任务（没有则 None）。"""
        ready = self.next_ready()
        return ready[0] if ready else None
