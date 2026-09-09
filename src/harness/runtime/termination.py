# -*- coding: utf-8 -*-
"""
harness/runtime/termination.py —— Termination Policy（DEV_PLAN A4 / 步骤 17 配套）

统一终止原因命名；实际判定逻辑在 Agent Runtime 与 Loop Guard 中消费。
"""

from __future__ import annotations

# 终止原因（A4 定义）
SUCCESS = "success"
MAX_ITERATIONS = "max_iterations"
BUDGET_EXCEEDED = "budget_exceeded"
REPEATED_FAILURE = "repeated_failure"
HUMAN_STOP = "human_stop"
UNRECOVERABLE_ERROR = "unrecoverable_error"

ALL_REASONS = (SUCCESS, MAX_ITERATIONS, BUDGET_EXCEEDED, REPEATED_FAILURE,
               HUMAN_STOP, UNRECOVERABLE_ERROR)


def reason_from_timeout_note(final_text: str) -> str:
    """Loop Guard 写入的占位结尾若存在，说明是迭代上限终止。"""
    return MAX_ITERATIONS if "已达最大迭代限制" in (final_text or "") else SUCCESS
