# -*- coding: utf-8 -*-
"""
harness/control/guardrails.py —— 三层 Guardrail（DEV_PLAN H7 / 步骤 90-92）

Input / Tool / Output 各设检查点：通过返回 issues=[]，否则列出问题。
Tool 层复用 policy（权限/风险）+ schema 校验；输出层支持自定义 schema 校验函数。
"""

from __future__ import annotations

from src.harness.control.policy import allowed
from src.harness.tools.registry import ToolRegistry
from src.harness.tools.schema import validate_arguments


def check_input(text: str, *, max_len: int = 8000, forbid: tuple = ()) -> list[str]:
    issues = []
    if len(text) > max_len:
        issues.append(f"输入超长（{len(text)} > {max_len}）")
    for token in forbid:
        if token in text:
            issues.append(f"输入包含禁用内容：{token}")
    return issues


def check_tool(name: str, args: dict, *,
               registry: ToolRegistry | None = None,
               permissions=None, deny: tuple = ()) -> tuple[bool, str]:
    if name in deny:
        return False, f"工具 {name} 在 deny 名单"
    spec = (registry or ToolRegistry.with_builtins()).get(name)
    if spec is None:
        return False, f"工具 {name} 未注册"
    err = validate_arguments(spec.parameters, args or {})
    if err:
        return False, f"参数校验失败：{err}"
    ok, reason = allowed(spec, permissions)
    if not ok:
        return False, reason
    return True, ""


def check_output(text: str, *, min_len: int = 0, forbid: tuple = (),
                 schema=None, schema_validator=None) -> list[str]:
    issues = []
    if len(text or "") < min_len:
        issues.append(f"输出过短（{len(text or '')} < {min_len}）")
    for token in forbid:
        if token in (text or ""):
            issues.append(f"输出包含禁用内容：{token}")
    if schema and schema_validator:
        err = schema_validator(text, schema)
        if err:
            issues.append(f"输出不符合 schema：{err}")
    return issues
