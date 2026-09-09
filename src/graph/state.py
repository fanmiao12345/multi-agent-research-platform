# -*- coding: utf-8 -*-
"""
graph/state.py —— 统一 AgentState（DEV_PLAN 3.1 / Milestone 1 步骤 20）

原则（文档 3）：不要把全部内容塞进 messages。Graph State 只保存当前 Thread
里参与流程控制的数据；Run 的静态参数进 RuntimeContext；中间产物进 Workspace。

字段分三组：
- 流程控制：run_id / status / iteration / max_iterations / task_type / plan…
- 对话与执行流：messages / tool_events / agent_outputs / errors（append Reducer）
- 结果与度量：metrics / artifacts / evidence（逐步启用，先声明占位）

注意：并行写字段必须走 Reducer（见 graph/reducers.py）。
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import MessagesState  # noqa: F401 —— 学习参考：官方 messages state

from src.graph.reducers import AppendReducer, MessagesReducer


class AgentState(TypedDict, total=False):
    """统一 Agent Graph State（total=False：节点按需使用字段，未用字段不强制提供）。"""

    # ---- 流程控制 ----
    run_id: str
    iteration: int
    max_iterations: int
    status: str                    # running / completed / failed / cancelled
    termination_reason: str        # success / max_iterations / budget_exceeded / ...
    user_task: str
    task_type: str                 # plain / tool / research / plan / multi ...

    # ---- 计划（Milestone 3 启用）----
    plan: list
    current_task_id: str | None
    completed_task_ids: list
    failed_task_ids: list

    # ---- 执行流（append Reducer，允许并行写）----
    messages: MessagesReducer
    tool_events: AppendReducer
    agent_outputs: AppendReducer
    errors: AppendReducer

    # ---- 产物与度量（Milestone 后段逐步启用）----
    active_agent: str | None
    evidence: AppendReducer
    artifacts: AppendReducer
    metrics: dict


def new_state(user_task: str, run_id: str = "", max_iterations: int = 5) -> dict:
    """构造一次运行的初始 State（统一从这里出发，避免各处手写漏字段）。"""
    return {
        "run_id": run_id,
        "iteration": 0,
        "max_iterations": max_iterations,
        "status": "running",
        "termination_reason": "",
        "user_task": user_task,
        "task_type": "",
        "messages": [{"role": "user", "content": user_task}],
    }
