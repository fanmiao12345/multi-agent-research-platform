# -*- coding: utf-8 -*-
"""
S8-01 执行方案契约：调度智能体的输出边界（模式/角色/子任务/预算/降级）。

首版收敛（设计文档 2.B）：auto 只在 fixed 与 fanout 之间选型；其余模式验收一个
开放一个。解析失败即拒绝（PlanValidationError），由调用方降级，不猜测。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# 当前已开放模式；dynamic_team/debate 在 D6 后半开放
FIRST_VERSION_MODES: tuple[str, ...] = ("single", "fixed", "manager_worker", "fanout", "dynamic_team")
# 全量目录（调度提示词展示用，但候选按 allowed_modes 过滤——按能力选型，不是见名就选）
KNOWN_MODES: tuple[str, ...] = ("fixed", "fanout", "manager_worker", "debate",
                                "dynamic_team", "single")
KNOWN_ROLES: tuple[str, ...] = ("researcher", "organizer", "writer", "editor", "agent")
MAX_SUBTASKS = 12          # 派生总数上限（含方案子任务与嵌套派生合计）
MAX_PARALLEL = 3           # 单层并发上限
DEFAULT_FALLBACK = "fixed"
SCHEMA_VERSION = "1"


class PlanValidationError(ValueError):
    """方案非法：调用方应降级到保底模式，不得带病执行。"""


@dataclass(frozen=True)
class Budget:
    """一次运行的钱闸（与 TaskRequest 的 max_* 字段一一对应）。"""
    max_calls: int = 0
    max_cost_usd: float = 0.0
    max_seconds: float = 0.0

    def __post_init__(self):
        for name in ("max_calls",):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise PlanValidationError("budget.max_calls 必须为非负整数")
        for name in ("max_cost_usd", "max_seconds"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) \
                    or not math.isfinite(v) or v < 0:
                raise PlanValidationError(f"budget.{name} 必须为非负有限数")

    def clamp_to(self, caps: "Budget") -> "Budget":
        """执行器按实际值取最小值：方案预算不得超过用户/系统给定上限。"""
        return Budget(max_calls=min(self.max_calls, caps.max_calls),
                      max_cost_usd=round(min(self.max_cost_usd, caps.max_cost_usd), 6),
                      max_seconds=min(self.max_seconds, caps.max_seconds))

    def subtract(self, other: "Budget") -> "Budget":
        """字段相减、下限 0：用于"根剩余 − 预留"。"""
        return Budget(max_calls=max(0, self.max_calls - other.max_calls),
                      max_cost_usd=round(max(0.0, self.max_cost_usd - other.max_cost_usd), 6),
                      max_seconds=max(0.0, self.max_seconds - other.max_seconds))

    def is_zero(self) -> bool:
        return self.max_calls <= 0 or self.max_cost_usd <= 0.0 or self.max_seconds <= 0.0

    def as_dict(self) -> dict:
        return {"max_calls": self.max_calls, "max_cost_usd": self.max_cost_usd,
                "max_seconds": self.max_seconds}


@dataclass(frozen=True)
class SubTask:
    id: str
    role: str
    description: str
    depends_on: tuple[str, ...] = ()
    parallel: bool = False
    covers_sections: tuple[str, ...] = ()   # 子任务与必需章节的映射（程序校验覆盖）


@dataclass(frozen=True)
class ExecutionPlan:
    schema_version: str
    mode: str
    reason: str
    subtasks: tuple[SubTask, ...] = ()
    needs_reviewer: bool = True
    max_parallel: int = 1
    budget: Budget = field(default_factory=Budget)
    fallback_mode: str = DEFAULT_FALLBACK
    expected: dict = field(default_factory=dict)          # 预计 calls/cost/seconds（展示用）
    complexity_signals: dict = field(default_factory=dict)  # 选型判据读数（可审计）

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version, "mode": self.mode,
            "reason": self.reason,
            "complexity_signals": dict(self.complexity_signals),
            "subtasks": [{"id": s.id, "role": s.role, "description": s.description,
                          "depends_on": list(s.depends_on), "parallel": s.parallel,
                          "covers_sections": list(s.covers_sections)}
                         for s in self.subtasks],
            "needs_reviewer": self.needs_reviewer, "max_parallel": self.max_parallel,
            "budget": self.budget.as_dict(), "fallback_mode": self.fallback_mode,
            "expected": dict(self.expected),
        }


def from_plan_dict(data: dict, *, allowed_modes: tuple[str, ...] = FIRST_VERSION_MODES) -> ExecutionPlan:
    """解析并校验方案；任何非法项都抛 PlanValidationError（含全部错误清单）。"""
    if not isinstance(data, dict):
        raise PlanValidationError("方案必须为JSON对象")
    errors: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version 必须为 {SCHEMA_VERSION}")
    mode = data.get("mode")
    if mode not in allowed_modes:
        errors.append(f"mode 必须取已开放模式 {list(allowed_modes)}（收到 {mode!r}）")
    fallback = data.get("fallback_mode") or DEFAULT_FALLBACK
    if fallback not in allowed_modes:
        errors.append(f"fallback_mode 必须取已开放模式 {list(allowed_modes)}")
    if not str(data.get("reason") or "").strip():
        errors.append("reason 必须为非空文本（可审计的选型理由）")

    raw_subtasks = data.get("subtasks") or []
    if not isinstance(raw_subtasks, list) or not (1 <= len(raw_subtasks) <= MAX_SUBTASKS):
        errors.append(f"subtasks 必须为 1~{MAX_SUBTASKS} 个的列表")
        raw_subtasks = []
    subtasks: list[SubTask] = []
    ids: set[str] = set()
    for i, raw in enumerate(raw_subtasks):
        if not isinstance(raw, dict):
            errors.append(f"subtasks[{i}] 必须为对象")
            continue
        sid = str(raw.get("id") or "").strip()
        role = str(raw.get("role") or "").strip()
        desc = str(raw.get("description") or "").strip()
        if not sid:
            errors.append(f"subtasks[{i}].id 必须为非空文本")
        elif sid in ids:
            errors.append(f"subtasks[{i}].id 重复：{sid}")
        else:
            ids.add(sid)
        if role not in KNOWN_ROLES:
            errors.append(f"subtasks[{i}].role 必须取 {list(KNOWN_ROLES)}（收到 {role!r}）")
        if not desc:
            errors.append(f"subtasks[{i}].description 必须为非空文本")
        covers = raw.get("covers_sections") or []
        if not isinstance(covers, list) or not all(isinstance(c, str) and c.strip() for c in covers):
            errors.append(f"subtasks[{i}].covers_sections 必须为非空文本列表")
            covers = []
        depends = raw.get("depends_on") or []
        if not isinstance(depends, list) or not all(isinstance(d, str) for d in depends):
            errors.append(f"subtasks[{i}].depends_on 必须为文本列表")
            depends = []
        subtasks.append(SubTask(id=sid or f"T{i + 1}", role=role, description=desc,
                                depends_on=tuple(depends), parallel=bool(raw.get("parallel")),
                                covers_sections=tuple(covers)))
    for st in subtasks:
        for dep in st.depends_on:
            if dep == st.id:
                errors.append(f"subtasks[{st.id}] 依赖自身")
            elif dep not in ids:
                errors.append(f"subtasks[{st.id}] 依赖未定义的子任务 {dep!r}")

    # 依赖必须有向无环；循环计划不得进入执行器。
    graph = {st.id: list(st.depends_on) for st in subtasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in visited:
            return
        if node in visiting:
            errors.append("子任务依赖存在循环：" + " → ".join(path + [node]))
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            if dep in graph:
                visit(dep, path + [node])
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node, [])

    max_parallel = data.get("max_parallel", 1)
    if isinstance(max_parallel, bool) or not isinstance(max_parallel, int) \
            or not (1 <= max_parallel <= MAX_PARALLEL):
        errors.append(f"max_parallel 必须为 1~{MAX_PARALLEL} 的整数")
        max_parallel = 1
    raw_budget = data.get("budget") or {}
    if not isinstance(raw_budget, dict):
        errors.append("budget 必须为对象")
        raw_budget = {}
    try:
        budget = Budget(max_calls=int(raw_budget.get("max_calls", 0) or 0),
                        max_cost_usd=float(raw_budget.get("max_cost_usd", 0.0) or 0.0),
                        max_seconds=float(raw_budget.get("max_seconds", 0.0) or 0.0))
    except (TypeError, ValueError, PlanValidationError) as e:
        errors.append(f"budget 字段非法：{e}")
        budget = Budget()
    expected = data.get("expected") or {}
    if not isinstance(expected, dict):
        errors.append("expected 必须为对象")
        expected = {}
    signals = data.get("complexity_signals") or {}
    if not isinstance(signals, dict):
        errors.append("complexity_signals 必须为对象")
        signals = {}
    if errors:
        raise PlanValidationError("；".join(errors))
    return ExecutionPlan(schema_version=str(data["schema_version"]), mode=mode,
                         reason=str(data["reason"]), subtasks=tuple(subtasks),
                         needs_reviewer=bool(data.get("needs_reviewer", True)),
                         max_parallel=max_parallel, budget=budget,
                         fallback_mode=fallback, expected=expected,
                         complexity_signals=signals)
