# -*- coding: utf-8 -*-
"""
harness/control/errors.py —— 错误分类（DEV_PLAN H4 / 步骤 86）

统一五类错误：transient / validation / permission / rate_limit / timeout / permanent。
Retry 决策与 Loop Guard 都依据分类，而不是看异常字符串。
"""

from __future__ import annotations

import urllib.error

TRANSIENT = "transient"
VALIDATION = "validation"
PERMISSION = "permission"
RATE_LIMIT = "rate_limit"
TIMEOUT = "timeout"
PERMANENT = "permanent"

ALL = (TRANSIENT, VALIDATION, PERMISSION, RATE_LIMIT, TIMEOUT, PERMANENT)
RETRYABLE = {TRANSIENT, TIMEOUT, RATE_LIMIT}


def classify(e: Exception | str) -> str:
    """把异常（或错误文本）映射到分类。"""
    text = str(e)
    if isinstance(e, PermissionError):
        return PERMISSION
    if isinstance(e, TimeoutError):
        return TIMEOUT
    if isinstance(e, (ValueError, TypeError)):
        return VALIDATION
    if isinstance(e, urllib.error.URLError) or isinstance(e, ConnectionError):
        return TRANSIENT
    if "rate" in text.lower() or "限流" in text or "429" in text:
        return RATE_LIMIT
    if "timed out" in text.lower() or "超时" in text:
        return TIMEOUT
    if text.startswith("[tool-permission-denied]") or "未授权" in text:
        return PERMISSION
    return PERMANENT


def is_retryable(category: str) -> bool:
    return category in RETRYABLE
