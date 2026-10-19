# -*- coding: utf-8 -*-
"""
harness/memory/checkpointer.py —— Checkpointer 接线（DEV_PLAN F1/66，H1 基础）

短期记忆 = Graph State + Thread + Checkpointer：
同一 AgentRuntime 实例内，相同 thread_id 的多轮 invoke 会带上历史。

注意：make_checkpointer() 当前返回 MemorySaver，状态只在当前进程内存中。
进程退出后 checkpoint 不保留，也不会在重启时自动恢复图会话或任务；
跨进程研究恢复由业务层（SQLite 状态 + stage_*.json + 显式 resume）负责。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver


def make_checkpointer():
    """进程内 checkpointer：同一实例可复用 thread 状态，不跨进程持久化。"""
    return MemorySaver()


def thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}
