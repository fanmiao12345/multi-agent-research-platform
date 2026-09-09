# -*- coding: utf-8 -*-
"""
harness/control/policies.py —— Retry & Timeout Policy（DEV_PLAN H4/H5 / 步骤 87-88）

- RetryPolicy：按错误分类决定是否重试（默认只重试 transient/timeout/rate_limit）
- TimeoutPolicy：分工具/节点/任务/运行四层上限
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.harness.control.errors import RETRYABLE, classify


@dataclass
class RetryPolicy:
    max_attempts: int = 2                  # 总尝试次数（含首次）
    retry_on: frozenset = frozenset(RETRYABLE)
    backoff_base: float = 0.1

    @classmethod
    def from_int(cls, n: int) -> "RetryPolicy":
        """兼容旧的 retry_policy 整数写法：n = 额外重试次数。"""
        return cls(max_attempts=n + 1) if n else cls(max_attempts=1, retry_on=frozenset())

    def should_retry(self, category: str, attempt: int) -> bool:
        return attempt < self.max_attempts - 1 and category in self.retry_on

    def backoff(self, attempt: int) -> float:
        return self.backoff_base * (2 ** attempt)


@dataclass
class TimeoutPolicy:
    tool: float | None = None
    node: float | None = None
    task: float | None = None
    run: float | None = None


def exceeded(started_at: float, limit: float | None) -> bool:
    """按单调时钟判断是否超时（limit=None 永不过期）。"""
    if limit is None:
        return False
    return (time.perf_counter() - started_at) >= limit


def enforce_classify(e) -> str:
    """供运行时统一使用：旧 classify_error 的出口。"""
    return classify(e)
