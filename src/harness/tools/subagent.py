# -*- coding: utf-8 -*-
"""
harness/tools/subagent.py —— Subagent as Tool（DEV_PLAN D10 / 步骤 54）

把“派生子智能体”做成普通工具：
    delegate_subagent(role, task, budget)
Main Agent 在循环中随时临时派生一个独立子 Agent（独立 run + workspace），
拿到它的最终文本作为工具结果继续自己的任务。

结构：
    Main Agent → delegate_subagent → AgentRuntime.run_task（子 Run）
    → 结构化文本结果 → 回到 Main Agent

风险：MEDIUM、side_effect=True（会创建子 Run）；子 Run 自带循环保护，
不存在无限递归（子 Run 的工具集不包含 delegate）。
"""

from __future__ import annotations

from src.harness.tools.registry import RISK_MEDIUM, ToolRegistry, ToolSpec
from src.harness.model_gateway import role_scope, BudgetStop

ALLOWED_ROLES = ("researcher", "organizer", "writer", "editor", "agent")


def build_delegate_spec(runtime, allowed_roles: tuple = ALLOWED_ROLES) -> ToolSpec:
    """把一个 AgentRuntime 包装成 delegate_subagent 工具。"""

    def delegate_subagent(role: str, task: str, budget: int = 5) -> str:
        if role not in allowed_roles:
            return (f"错误：不支持的子智能体角色 {role}（可用：{', '.join(allowed_roles)}）")
        from src.harness.runtime.run_context import RuntimeContext

        ctx = RuntimeContext.from_settings(max_iterations=int(budget))
        with role_scope(role):
            outcome = runtime.run_task(f"[{role} 子任务] {task}", context=ctx)
        if outcome.termination_reason == "budget_exceeded":
            raise BudgetStop("根任务限制已触发")
        return (f"（子智能体 {role} 完成，状态={outcome.status}，"
                f"原因={outcome.termination_reason}）\n{outcome.final_text}")

    return ToolSpec(
        name="delegate_subagent",
        description="临时派生子智能体执行一个子任务，等它完成后把结果带回。"
                    "role 可选 researcher/organizer/writer/editor/agent；task 是子任务描述；"
                    "budget 是子任务最大迭代轮数。",
        func=delegate_subagent,
        parameters={"type": "object",
                    "properties": {
                        "role": {"type": "string", "description": "子智能体角色"},
                        "task": {"type": "string", "description": "子任务描述"},
                        "budget": {"type": "integer", "description": "最大轮数，默认 5"},
                    },
                    "required": ["role", "task"]},
        tags=("subagent", "orchestration"),
        risk_level=RISK_MEDIUM,
        side_effect=True)


def register_subagent_tool(registry: ToolRegistry, runtime) -> str:
    """把 delegate 工具注册进 registry；返回工具名。"""
    spec = build_delegate_spec(runtime)
    registry.register(spec)
    return spec.name
