# -*- coding: utf-8 -*-
"""
harness/runtime/lifecycle.py —— Agent 生命周期（DEV_PLAN A1 / 步骤 18）

状态机：
    CREATED → RUNNING → WAITING_TOOL / WAITING_HUMAN → RUNNING → COMPLETED/FAILED/CANCELLED

每次运行记录：run_id / thread_id / status / started_at / ended_at / termination_reason。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

CREATED = "CREATED"
RUNNING = "RUNNING"
WAITING_TOOL = "WAITING_TOOL"
WAITING_HUMAN = "WAITING_HUMAN"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"

# 合法迁移表：当前状态 → 允许到达的状态
_TRANSITIONS: dict[str, set[str]] = {
    CREATED: {RUNNING, CANCELLED},
    RUNNING: {WAITING_TOOL, WAITING_HUMAN, COMPLETED, FAILED, CANCELLED},
    WAITING_TOOL: {RUNNING, FAILED},
    WAITING_HUMAN: {RUNNING, CANCELLED},
    # 终态不可再迁移
    COMPLETED: set(),
    FAILED: set(),
    CANCELLED: set(),
}


@dataclass
class RunRecord:
    """一次运行的生命周期档案（与 run.json 互补：这里专注状态机语义）。"""

    run_id: str = ""
    thread_id: str = ""
    status: str = CREATED
    started_at: str = ""
    ended_at: str = ""
    termination_reason: str = ""

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not self.thread_id:
            self.thread_id = self.run_id

    def transition(self, new_status: str, reason: str = "") -> "RunRecord":
        if new_status == self.status:
            return self
        if new_status not in _TRANSITIONS.get(self.status, set()):
            raise ValueError(f"非法生命周期迁移：{self.status} → {new_status}")
        self.status = new_status
        if new_status in (COMPLETED, FAILED, CANCELLED):
            self.ended_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        if reason:
            self.termination_reason = reason
        return self
