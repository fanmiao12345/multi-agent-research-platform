# -*- coding: utf-8 -*-
"""D6-07：子智能体工具——最多二层嵌套、数量/去重/权限继承。"""
from __future__ import annotations

import contextvars
import re

from src.harness.budget_control import IterationBudget
from src.harness.model_gateway import role_scope, BudgetStop
from src.harness.tools.registry import RISK_MEDIUM, ToolRegistry, ToolSpec

ALLOWED_ROLES = ("researcher", "organizer", "writer", "editor", "agent")
_DEPTH = contextvars.ContextVar("subagent_depth", default=0)
_STATE = contextvars.ContextVar("subagent_state", default=None)
_PERMISSIONS = contextvars.ContextVar("subagent_permissions", default=None)


class _SubagentState:
    def __init__(self, max_total: int = 12):
        self.seen: set[str] = set()
        self.count = 0
        # 父级迭代预算 = 派生总数上限；同时带熔断器（连续失败 3 次即 open）
        self.budget = IterationBudget(max_iterations=max_total,
                                      max_consecutive_failures=3)


def _normalize_task(task: str) -> str:
    return re.sub(r"[\s，。；：、！？,. ; : ! ?\n\t]+", "", (task or "").lower())


class subagent_permission_scope:
    """把当前 Agent 的有效工具权限传给 delegate_subagent 子运行。"""

    def __init__(self, permissions):
        self.permissions = frozenset(permissions or ())
        self.token = None
        self.state_token = None

    def __enter__(self):
        self.token = _PERMISSIONS.set(self.permissions)
        self.state_token = _STATE.set(_SubagentState())
        return self

    def __exit__(self, exc_type, exc, tb):
        _STATE.reset(self.state_token)
        _PERMISSIONS.reset(self.token)


def build_delegate_spec(runtime, allowed_roles: tuple = ALLOWED_ROLES, *,
                        max_depth: int = 2, max_total: int = 12) -> ToolSpec:
    """把一个 AgentRuntime 包装成受控 delegate_subagent 工具。"""

    def delegate_subagent(role: str, task: str, budget: int = 5) -> str:
        if role not in allowed_roles:
            return (f"错误：不支持的子智能体角色 {role}（可用：{', '.join(allowed_roles)}）")
        if not (task or "").strip():
            return "错误：子任务描述不能为空"
        depth = _DEPTH.get()
        if depth >= max_depth:
            return f"[depth-limit] 子智能体嵌套深度超限（已到第 {depth} 层，最多 {max_depth} 层）"
        state = _STATE.get()
        state_token = None
        if state is None:
            state = _SubagentState()
            state_token = _STATE.set(state)
        key = _normalize_task(task)
        if key and key in state.seen:
            if state_token is not None:
                _STATE.reset(state_token)
            return "[duplicate] 检测到同题重复派生，已拒绝（防止自激循环）"
        if state.count + 1 > max_total:
            if state_token is not None:
                _STATE.reset(state_token)
            return f"[count-limit] 子智能体派生总数超过上限 {max_total}"
        if key:
            state.seen.add(key)
        state.count += 1

        # IterationBudget：父级熔断器。连续失败达到阈值 → open，
        # 之后所有派生拒绝（防止自激空转烧预算）；换路降级由上层 try_reclose()。
        parent_budget = state.budget
        if parent_budget.state == "open" or parent_budget.exhausted:
            if state_token is not None:
                _STATE.reset(state_token)
            return ("[circuit-open] 子智能体派生预算耗尽或连续失败已触发熔断，"
                    f"本轮拒绝继续派生（累计失败 {parent_budget.total_failures} 次）")

        from src.harness.runtime.run_context import RuntimeContext

        permissions = _PERMISSIONS.get()
        kwargs = {"max_iterations": max(1, int(budget))}
        if permissions is not None:
            kwargs["permissions"] = permissions
        ctx = RuntimeContext.from_settings(**kwargs)
        depth_token = _DEPTH.set(depth + 1)
        try:
            with role_scope(role):
                outcome = runtime.run_task(f"[{role} 子任务] {task}", context=ctx)
            parent_budget.consume(1)
            if outcome.termination_reason in ("budget_exceeded", "unrecoverable_error",
                                              "error"):
                parent_budget.record_failure()
            else:
                parent_budget.record_success()
            if outcome.termination_reason == "budget_exceeded":
                raise BudgetStop("根任务限制已触发")
            return (f"（子智能体 {role} 完成，层级={depth + 1}，状态={outcome.status}，"
                    f"原因={outcome.termination_reason}）\n{outcome.final_text}")
        except BudgetStop:
            raise
        except Exception:  # noqa: BLE001 —— 失败也记账，喂给熔断器
            parent_budget.record_failure()
            raise
        finally:
            _DEPTH.reset(depth_token)
            if state_token is not None:
                _STATE.reset(state_token)

    return ToolSpec(
        name="delegate_subagent",
        description="临时派生子智能体执行一个子任务，等它完成后把结果带回。"
                    "role 可选 researcher/organizer/writer/editor/agent；task 是子任务描述；"
                    "budget 是子任务最大迭代轮数。最多二层嵌套，同题不重复派工。",
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


def register_subagent_tool(registry: ToolRegistry, runtime, **kwargs) -> str:
    """把 delegate 工具注册进 registry；返回工具名。"""
    spec = build_delegate_spec(runtime, **kwargs)
    registry.register(spec)
    return spec.name