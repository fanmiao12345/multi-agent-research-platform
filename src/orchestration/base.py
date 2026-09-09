# -*- coding: utf-8 -*-
"""
orchestration —— 统一 Harness 上的 Orchestration Policies（M7 步骤 75-82）。

所有策略共享同一批原语（AgentRuntime + Profile + ToolRegistry + Context），
策略只决定"谁先谁后、并行度、交接与验收" —— D-007 的落地。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from src.agents.profiles import AgentProfile
from src.harness.model_gateway import role_scope, BudgetStop

# worker(task_text, role_name) -> str：策略模块只依赖这个接口（可注入 stub/真实）
Worker = Callable[[str, str], str]


def make_worker(runtime, profiles: dict | None = None) -> Worker:
    """默认 worker：按 profile 注入人设后跑一次 AgentRuntime。"""
    profiles = profiles or {}

    def worker(task_text: str, role: str) -> str:
        extra = ""
        profile = profiles.get(role)
        if profile is not None:
            extra = profile.prompt
        with role_scope(role):
            outcome = runtime.run_task(task_text, system_extra=extra)
        if outcome.termination_reason == "budget_exceeded":
            raise BudgetStop("根任务限制已触发，停止后续阶段")
        return outcome.final_text

    return worker


@dataclass
class StrategyResult:
    name: str
    final: str
    worker_calls: int = 0
    stages: list = field(default_factory=list)


def pack_card(goal: str, stage: str, prev_output: str | None) -> str:
    """把上一棒产物做成 Handoff 卡片头（E6：只传必要内容，不传整段对话）。"""
    if not prev_output:
        return f"【目标】{goal}\n【本棒】{stage}\n"
    snippet = prev_output.strip().replace("\n", " ")[:400]
    return f"【目标】{goal}\n【本棒】{stage}\n【上一棒产物摘要】{snippet}\n"
