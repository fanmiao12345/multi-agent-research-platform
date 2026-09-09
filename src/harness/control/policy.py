# -*- coding: utf-8 -*-
"""
harness/control/policy.py —— Tool 风险 / 权限 / 审批策略（步骤 45-46）

规则集中在这里，Executor 只负责执行策略，不自己发明决策：
- 风险分级：LOW(读/查/算) < MEDIUM(写/草稿) < HIGH(删除/发送/外部变更)
- HIGH 默认需要 HITL 审批（approver）；MEDIUM 按上下文权限放行
- select_tools 按 role/task 标签 + 风险上限 + allowlist 做“动态工具选择”（47）
"""

from __future__ import annotations

from src.harness.tools.registry import (RISK_HIGH, RISK_LOW, RISK_MEDIUM,
                                        ToolRegistry, ToolSpec)

_RISK_ORDER = {RISK_LOW: 0, RISK_MEDIUM: 1, RISK_HIGH: 2}


def risk_level_of(spec: ToolSpec) -> int:
    return _RISK_ORDER.get(spec.risk_level, 1)


def requires_approval(spec: ToolSpec) -> bool:
    """高风险或显式声明审批，任一条件满足就必须 HITL。"""
    return spec.risk_level == RISK_HIGH or spec.requires_approval


def allowed(spec: ToolSpec, permissions: set | frozenset | None,
            approver=None) -> tuple[bool, str]:
    """返回 (是否允许, 原因)。Executor 的决策前置检查统一走这里。"""
    if requires_approval(spec) and not approver:
        return False, "高风险工具需要人工审批（HITL），当前无审批者"
    if permissions is not None and spec.name not in permissions:
        return False, f"未授权调用 {spec.name}"
    return True, ""


def select_tools(registry: ToolRegistry, *, role: str = "", task_tags=(),
                 risk_max: str = RISK_MEDIUM, allowlist: list[str] | None = None,
                 deny: tuple = ()) -> list[ToolSpec]:
    """动态工具选择（47）：按 角色/任务标签/风险上限/allowlist 过滤。

    说明：当前工具元信息里 tags 承担“能力标签”角色；role 匹配工具名或 tags。
    未来 D5 完整矩阵：Role→Task→Skill→Permissions→Stage 五维过滤在此扩展。
    """
    cap = _RISK_ORDER.get(risk_max, 1)
    out = []
    for spec in registry.list():
        if spec.name in deny:
            continue
        if allowlist is not None and spec.name not in allowlist:
            continue
        if risk_level_of(spec) > cap:
            continue
        if role and role != spec.name and role not in spec.tags:
            continue  # 角色未声明使用该工具（宽松：不在声明集就排除）
        if task_tags and not (set(task_tags) & set(spec.tags)):
            continue
        out.append(spec)
    return out
