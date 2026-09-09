# -*- coding: utf-8 -*-
"""
harness/state/states.py —— 任务状态词与映射（S4-02）

业务状态（job 层）：queued / running / waiting_human / cancel_requested /
completed / partial / failed / cancelled / interrupted。

与底层 Run 状态（lifecycle.py 大写词、run.json status 小写词）显式映射，
不靠字符串相等猜测；终态不可回写。
"""
from __future__ import annotations

QUEUED = "queued"
RUNNING = "running"
WAITING_HUMAN = "waiting_human"
CANCEL_REQUESTED = "cancel_requested"
COMPLETED = "completed"
PARTIAL = "partial"
FAILED = "failed"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"

ALL = (QUEUED, RUNNING, WAITING_HUMAN, CANCEL_REQUESTED, COMPLETED,
       PARTIAL, FAILED, CANCELLED, INTERRUPTED)
TERMINAL = (COMPLETED, PARTIAL, FAILED, CANCELLED)

# job 层状态 → run.json/底层 Run 状态（小写）的权威映射
RUN_LOWER = {"completed": COMPLETED, "failed": FAILED, "cancelled": CANCELLED,
             "running": RUNNING, "waiting_human": WAITING_HUMAN}

# 允许的迁移（保守：错误状态需要人工核对而不是自动跳跃）
_TRANSITIONS = {
    QUEUED: {RUNNING, CANCELLED, FAILED},
    RUNNING: {COMPLETED, PARTIAL, FAILED, CANCELLED, WAITING_HUMAN,
              CANCEL_REQUESTED, INTERRUPTED},
    WAITING_HUMAN: {RUNNING, CANCELLED, CANCEL_REQUESTED, FAILED},
    CANCEL_REQUESTED: {CANCELLED, PARTIAL, FAILED, COMPLETED},
    INTERRUPTED: {RUNNING, CANCELLED, FAILED, PARTIAL},
    COMPLETED: set(),
    PARTIAL: set(),
    FAILED: set(),
    CANCELLED: set(),
}


class StateError(ValueError):
    """非法状态迁移/未知状态。"""


def validate(status: str) -> str:
    if status not in ALL:
        raise StateError(f"未知任务状态：{status!r}（允许 {ALL}）")
    return status


def can_transition(current: str, target: str) -> bool:
    validate(current)
    validate(target)
    return target in _TRANSITIONS[current]


def transition(current: str, target: str) -> str:
    if not can_transition(current, target):
        raise StateError(f"非法任务状态迁移：{current} → {target}")
    return target


def map_run_status(run_status: str) -> str:
    """run.json 小写终态 → job 层业务状态（partial 由业务层自行标注）。"""
    mapped = RUN_LOWER.get((run_status or "").lower())
    if mapped is None:
        raise StateError(f"无法映射底层运行状态：{run_status!r}")
    return mapped


def is_terminal(status: str) -> bool:
    return status in TERMINAL
