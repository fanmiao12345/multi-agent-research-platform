# -*- coding: utf-8 -*-
"""
eval/evaluators/tool_metrics.py —— Tool Selection & Argument Eval（步骤 32/33）

从 trace.jsonl 的 tool_call 事件还原“实际用了哪些工具/参数”，对照任务期望算：
- selection_ok：期望工具集合 ⊆ 实际使用集合（多出且任务要求 no_tool 时判错）
- argument_ok：参数过 Tool Schema 校验，且 arg_eq 指定值逐一相等
"""

from __future__ import annotations

from src.harness.tools.registry import ToolRegistry
from src.harness.tools.schema import validate_arguments


def collect_tool_events(events: list[dict]) -> list[dict]:
    """从 trace 事件里取出 {name, arguments} 列表（保持调用顺序）。"""
    return [{"name": e["name"], "arguments": e.get("arguments") or {}}
            for e in events if e.get("type") == "tool_call"]


def evaluate(events: list[dict], expect: dict) -> dict:
    registry = ToolRegistry.with_builtins()
    used = collect_tool_events(events)
    used_names = [u["name"] for u in used]
    expected_tools = list(expect.get("tool") or [])
    no_tool_expected = bool(expect.get("no_tool"))

    selection_ok = True
    if no_tool_expected:
        selection_ok = not used_names
    elif expected_tools:
        selection_ok = set(expected_tools) <= set(used_names)

    # 参数校验：schema + arg_eq 逐字段比对
    argument_ok = True
    arg_errors: list[str] = []
    for u in used:
        spec = registry.get(u["name"])
        if spec is None:
            argument_ok = False
            arg_errors.append(f"{u['name']} 未注册")
            continue
        err = validate_arguments(spec.parameters, u["arguments"])
        if err:
            argument_ok = False
            arg_errors.append(f"{u['name']}: {err}")
            continue
        want = (expect.get("arg_eq") or {}).get(u["name"])
        if want:
            for key, value in want.items():
                if u["arguments"].get(key) != value:
                    argument_ok = False
                    arg_errors.append(f"{u['name']}.{key} 期望 {value}，实际 {u['arguments'].get(key)}")

    return {"used_tools": used_names, "tool_call_count": len(used),
            "selection_ok": selection_ok, "argument_ok": argument_ok,
            "arg_errors": arg_errors}
