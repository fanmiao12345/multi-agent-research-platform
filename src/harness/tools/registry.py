# -*- coding: utf-8 -*-
"""
harness/tools/registry.py —— Tool Registry（DEV_PLAN D2 / 步骤 23）

Agent 不直接 import 工具函数执行，而是通过 Registry 统一管理：
register / unregister / search / filter / get / list。
每个 Tool 带完整元信息（D1 的 Tool Schema）：risk_level / side_effect /
requires_approval / timeout / retry_policy —— Executor 将据此决策。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field

RISK_LOW, RISK_MEDIUM, RISK_HIGH = "LOW", "MEDIUM", "HIGH"


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的全部元信息（D1）。"""
    name: str
    description: str
    func: callable
    parameters: dict = field(default_factory=dict)   # OpenAI parameters（type/properties/required）
    tags: tuple = ()
    risk_level: str = RISK_LOW
    side_effect: bool = False
    requires_approval: bool = False
    timeout: float | None = None                     # 秒；None=不限
    retry_policy: int = 0                            # 自动重试次数（仅 transient 错误）

    def to_openai_tool(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.parameters}}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def search(self, query: str) -> list[ToolSpec]:
        q = query.lower()
        return [t for t in self._tools.values()
                if q in t.name.lower() or q in t.description.lower()
                or any(q in tag.lower() for tag in t.tags)]

    def filter(self, *, risk_levels=(), names=(), tags=(), side_effect=None) -> list[ToolSpec]:
        out = list(self._tools.values())
        if risk_levels:
            out = [t for t in out if t.risk_level in set(risk_levels)]
        if names:
            out = [t for t in out if t.name in set(names)]
        if tags:
            out = [t for t in out if set(tags) & set(t.tags)]
        if side_effect is not None:
            out = [t for t in out if t.side_effect is side_effect]
        return out

    def to_openai_tools(self, only: list[str] | None = None) -> list[dict]:
        specs = self._tools.values() if only is None else \
            [self._tools[n] for n in only if n in self._tools]
        return [s.to_openai_tool() for s in specs]

    # ---- 内置工具装配 ----
    @classmethod
    def with_builtins(cls) -> "ToolRegistry":
        from src.builtin_tools import TOOL_FUNCS  # noqa: PLC0415 —— 避免循环导入

        reg = cls()
        reg.register(ToolSpec(
            name="calculator",
            description="计算数学表达式，支持 + - * / // % ** 与括号，"
                        "以及 sin/cos/tan/sqrt/log/exp/round/abs、常数 pi/e。例子：'12*34+56'。",
            func=TOOL_FUNCS["calculator"],
            parameters={"type": "object",
                        "properties": {"expression": {"type": "string",
                                                      "description": "数学表达式"}},
                        "required": ["expression"]},
            tags=("math", "read")))
        reg.register(ToolSpec(
            name="current_time",
            description="获取当前的日期和时间（本地时间）。",
            func=TOOL_FUNCS["current_time"],
            parameters={"type": "object", "properties": {}},
            tags=("system", "read")))
        return reg


def describe(spec: ToolSpec) -> str:
    """给人看的工具说明（调试/审计用）。"""
    sig = str(inspect.signature(spec.func))
    return (f"{spec.name}{sig}  [risk={spec.risk_level}"
            + (", approval" if spec.requires_approval else "")
            + f", retry={spec.retry_policy}]  {spec.description[:60]}")
