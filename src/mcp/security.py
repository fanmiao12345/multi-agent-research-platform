# -*- coding: utf-8 -*-
"""
mcp/security.py —— MCP Security（DEV_PLAN J2 / 步骤 109）

MCP 工具进入 Harness 后与内置工具同待遇：
注册层做 Allowlist（工具级 allow/deny、风险映射），执行层仍走 ToolExecutor
的权限/风险/审批/超时链 —— 不允许绕过 Harness。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.harness.tools.registry import RISK_MEDIUM


@dataclass
class McpSecurity:
    allow_tools: set = field(default_factory=set)      # 空 = 全部允许（配合 deny）
    deny_tools: set = field(default_factory=set)
    tool_risk: dict = field(default_factory=dict)      # name -> LOW/MEDIUM/HIGH
    default_risk: str = RISK_MEDIUM
    # D2-04：未知外部操作默认 side_effect=True（不盲目自动重试）；
    # 已确认只读的工具可用 side_effect_tools 显式放开。
    default_side_effect: bool = True
    side_effect_tools: dict = field(default_factory=dict)   # name -> True/False

    def permits(self, tool_name: str) -> bool:
        if tool_name in self.deny_tools:
            return False
        if self.allow_tools and tool_name not in self.allow_tools:
            return False
        return True

    def side_effect_of(self, tool_name: str) -> bool:
        return self.side_effect_tools.get(tool_name, self.default_side_effect)

    def risk_of(self, tool_name: str) -> str:
        return self.tool_risk.get(tool_name, self.default_risk)
