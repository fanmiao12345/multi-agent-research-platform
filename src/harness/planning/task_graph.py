# -*- coding: utf-8 -*-
"""
harness/planning/task_graph.py —— Task Graph（DEV_PLAN C3 / 步骤 39）

在 Plan 之上计算依赖拓扑：哪些任务就绪（ready）、哪些被失败依赖阻塞（blocked）、
并行/串行/失败分支的判断都由这里提供（确定性逻辑，不交给 LLM）。
"""

from __future__ import annotations

from src.harness.planning.task import (BLOCKED, COMPLETED, FAILED, PENDING,
                                       READY, SKIPPED, Plan, Task)


def _dep_satisfied(dep_status: str) -> bool:
    return dep_status in (COMPLETED, SKIPPED)


class TaskGraph:
    def __init__(self, plan: Plan):
        self.plan = plan
        self._ids = {t.id for t in plan.tasks}

    def ready_tasks(self) -> list:
        """依赖全部完成/跳过的 PENDING 任务（按 priority 稳定排序）。"""
        by_id = self.plan.by_id
        out = []
        for t in self.plan.tasks:
            if t.status != PENDING:
                continue
            deps = [by_id(d) for d in t.depends_on if by_id(d)]
            if deps and not all(_dep_satisfied(d.status) for d in deps):
                continue  # 依赖未就绪（含依赖 PENDING/READY/RUNNING）
            out.append(t)
        return sorted(out, key=lambda t: (t.priority, t.id))

    def blocked_tasks(self) -> list:
        """存在 FAILED 依赖（且该依赖不打算重试）的 PENDING 任务。"""
        by_id = self.plan.by_id
        out = []
        for t in self.plan.tasks:
            if t.status != PENDING:
                continue
            deps = [by_id(d) for d in t.depends_on if by_id(d)]
            if deps and any(d.status == FAILED for d in deps):
                out.append(t)
        return sorted(out, key=lambda t: (t.priority, t.id))

    def is_finished(self) -> bool:
        return all(t.status in (COMPLETED, FAILED, SKIPPED) for t in self.plan.tasks)

    def all_completed(self) -> bool:
        return all(t.status == COMPLETED for t in self.plan.tasks)

    def dependencies_of(self, task_id: str) -> list:
        t = self.plan.by_id(task_id)
        if not t:
            return []
        return [self.plan.by_id(d) for d in t.depends_on if self.plan.by_id(d)]

    def children_of(self, task_id: str) -> list:
        return [t for t in self.plan.tasks if task_id in t.depends_on]

    def mark_blocked_cascade(self) -> None:
        """把“依赖已 FAILED 且无重试机会”的后继标成 BLOCKED（失败分支可视化）。"""
        by_id = self.plan.by_id
        for t in self.plan.tasks:
            if t.status == PENDING:
                deps = [by_id(d) for d in t.depends_on if by_id(d)]
                if deps and any(d.status == FAILED for d in deps):
                    t.status = BLOCKED

    def to_dict(self) -> dict:
        return self.plan.to_dict()
