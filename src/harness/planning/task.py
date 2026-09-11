# -*- coding: utf-8 -*-
"""
harness/planning/task.py —— Task / Plan 模型（DEV_PLAN C1/C2 / 步骤 37-38）

Plan 的结构（文档 C1）：
{
  "goal": "...",
  "tasks": [{"id": "T1", "description": "...", "depends_on": [],
             "preferred_agent": "researcher", "priority": 1, "status": "pending"}]
}

Task 状态机（文档 C2）：
PENDING → READY → RUNNING → COMPLETED / FAILED（FAILED 可被 Replanner 重置重试）
        （依赖失败未处理时保持 BLOCKED / 可选 SKIPPED）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PENDING = "PENDING"
READY = "READY"
RUNNING = "RUNNING"
BLOCKED = "BLOCKED"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
SKIPPED = "SKIPPED"

ALL_STATUSES = (PENDING, READY, RUNNING, BLOCKED, COMPLETED, FAILED, SKIPPED)
TERMINAL = (COMPLETED, FAILED, SKIPPED)

# 允许的状态迁移（reset 是 Replanner 专用旁路，不走这张表）
_TRANSITIONS = {
    PENDING: {READY, RUNNING, BLOCKED, SKIPPED},
    READY: {RUNNING, SKIPPED, PENDING},
    RUNNING: {COMPLETED, FAILED, PENDING},
    BLOCKED: {PENDING, READY, SKIPPED},
    COMPLETED: set(),
    FAILED: {PENDING, SKIPPED},   # Replanner 可把 FAILED 重置回 PENDING 重试
    SKIPPED: set(),
}


@dataclass
class Task:
    id: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    preferred_agent: str = "agent"
    priority: int = 1
    status: str = PENDING
    attempts: int = 0
    result: str = ""
    error: str = ""

    def transition(self, status: str) -> "Task":
        if status == self.status:
            return self
        if status not in _TRANSITIONS.get(self.status, set()):
            raise ValueError(f"非法 Task 状态迁移：{self.id} {self.status} → {status}")
        self.status = status
        return self

    def reset(self) -> "Task":
        """Replanner 重置：保留 attempts 计数防死循环。"""
        self.status = PENDING
        self.attempts += 1
        self.error = ""
        return self

    def to_dict(self) -> dict:
        return {"id": self.id, "description": self.description,
                "depends_on": list(self.depends_on),
                "preferred_agent": self.preferred_agent, "priority": self.priority,
                "status": self.status, "attempts": self.attempts,
                "result": self.result, "error": self.error}

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(id=d["id"], description=d["description"],
                   depends_on=list(d.get("depends_on") or []),
                   preferred_agent=d.get("preferred_agent", "agent"),
                   priority=int(d.get("priority", 1)),
                   status=str(d.get("status", PENDING)).upper(),
                   attempts=int(d.get("attempts", 0)),
                   result=d.get("result", ""), error=d.get("error", ""))


@dataclass
class Plan:
    goal: str
    tasks: list[Task] = field(default_factory=list)
    version: int = 1
    parent_version: int = 0
    invalidated_task_ids: list[str] = field(default_factory=list)

    def by_id(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def to_dict(self) -> dict:
        return {"goal": self.goal, "version": self.version,
                "parent_version": self.parent_version,
                "invalidated_task_ids": list(self.invalidated_task_ids),
                "tasks": [t.to_dict() for t in self.tasks]}

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        return cls(goal=d.get("goal", ""),
                   tasks=[Task.from_dict(t) for t in d.get("tasks", [])],
                   version=int(d.get("version", 1)),
                   parent_version=int(d.get("parent_version", 0)),
                   invalidated_task_ids=list(d.get("invalidated_task_ids") or []))

    @classmethod
    def from_llm_json(cls, data: dict, fallback_task: str) -> "Plan":
        """由 Planner 输出的 JSON 建 Plan；非法输入兜底为单任务计划。"""
        try:
            goal = str(data["goal"])
            raw_tasks = list(data["tasks"])
            tasks = []
            for raw in raw_tasks:
                tid = str(raw["id"]).strip()
                desc = str(raw["description"]).strip()
                if not tid or not desc:
                    raise ValueError("task id/description 缺失")
                tasks.append(Task(
                    id=tid,
                    description=desc,
                    depends_on=[str(x) for x in raw.get("depends_on", [])],
                    preferred_agent=str(raw.get("preferred_agent", "agent")),
                    priority=int(raw.get("priority", 1))))
            ids = {t.id for t in tasks}
            if len(ids) != len(tasks) or any(
                    dep not in ids for t in tasks for dep in t.depends_on):
                raise ValueError("task id 重复或 depends_on 引用不存在")
            return cls(goal=goal, tasks=tasks)
        except Exception:
            # 解析失败：绝不带着残缺计划执行 —— 退回单任务计划
            return cls(goal=fallback_task,
                       tasks=[Task(id="T1", description=fallback_task)])

    def summary(self) -> str:
        lines = [f"goal: {self.goal}"]
        for t in self.tasks:
            dep = f" <- {','.join(t.depends_on)}" if t.depends_on else ""
            lines.append(f"  {t.id} [{t.status}] {t.description}{dep}")
        return "\n".join(lines)
