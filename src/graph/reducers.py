# -*- coding: utf-8 -*-
"""
graph/reducers.py —— State 字段的合并策略（DEV_PLAN Milestone 1 / 步骤 21）

LangGraph 里并行/多节点写同一个字段时，必须定义 Reducer 决定如何合并。
本模块集中定义 Harness 会用到的 append 型 Reducer（未来可加 last-wins 等）。
"""

from __future__ import annotations

import operator
from typing import Annotated


def last_wins(current, update):
    """显式 last-wins（LangGraph 默认行为，写成函数便于阅读与单测）。"""
    return update


MessagesReducer = Annotated[list, operator.add]
AppendReducer = Annotated[list, operator.add]
