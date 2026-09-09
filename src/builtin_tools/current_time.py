# -*- coding: utf-8 -*-
"""内置工具：current_time（DEV_PLAN A0.6 步骤 11）。"""

from __future__ import annotations

import datetime


def current_time() -> str:
    """返回本地当前日期时间（工具输出统一为 str）。"""
    now = datetime.datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S（%A）")
