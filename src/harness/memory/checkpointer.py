# -*- coding: utf-8 -*-
"""
harness/memory/checkpointer.py —— Checkpointer 接线（DEV_PLAN F1/66，H1 基础）

短期记忆 = Graph State + Thread + Checkpointer：
同一 thread_id 的多轮 invoke 会带上历史（持久化在 MemorySaver），
这也是后续 H1/H2（进程中断后 resume）的地基。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver


def make_checkpointer():
    """内存版 checkpointer（开发期用；生产换 SQLite/Postgres 同接口）。"""
    return MemorySaver()


def thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}
