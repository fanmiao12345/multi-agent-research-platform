# -*- coding: utf-8 -*-
"""D6-08：六种协作方式统一注册表。"""
from __future__ import annotations

MODE_HANDLERS = {
    "single": "_execute_single",
    "fixed": "_execute_fixed",
    "manager_worker": "_execute_fanout",
    "fanout": "_execute_fanout",
    "dynamic_team": "_execute_dynamic_team",
    "debate": "_execute_debate",
}

MODE_LABELS = {
    "single": "单智能体",
    "fixed": "固定研究链",
    "manager_worker": "统筹者-工作者",
    "fanout": "并行分工",
    "dynamic_team": "动态团队",
    "debate": "正反辩论",
}


def handler_name(mode: str) -> str | None:
    return MODE_HANDLERS.get((mode or "").strip())