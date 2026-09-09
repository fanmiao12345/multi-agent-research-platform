# -*- coding: utf-8 -*-
"""
builtin_tools/__init__.py —— 内置工具注册（M0 最小版，Milestone 1 将升级为
harness/tools 的 Tool Registry + Executor，届时此处只提供函数与 schema）。

schema 采用 OpenAI function calling 格式，直接传给 LLM Adapter。
"""

from __future__ import annotations

from src.builtin_tools.calculator import calculator
from src.builtin_tools.current_time import current_time

TOOL_FUNCS: dict[str, callable] = {
    "calculator": calculator,
    "current_time": current_time,
}

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "计算数学表达式，支持 + - * / // % ** 与括号，"
                           "以及 sin/cos/tan/sqrt/log/exp/round/abs、常数 pi/e。"
                           "例子：'12*34+56'、'sqrt(2)+1'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "要计算的数学表达式"},
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "current_time",
            "description": "获取当前的日期和时间（本地时间）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def run_tool(name: str, arguments: dict) -> str:
    """M0 版工具执行入口：任何异常都转成文本（不崩溃，模型可据此调整）。"""
    fn = TOOL_FUNCS.get(name)
    if fn is None:
        return f"错误：没有名为 {name} 的工具。可用工具：{', '.join(TOOL_FUNCS)}"
    try:
        return str(fn(**arguments))
    except TypeError as e:
        return f"错误：调用 {name} 时参数不对：{e}"
    except Exception as e:  # noqa: BLE001
        return f"错误：工具 {name} 执行失败：{e}"
