# -*- coding: utf-8 -*-
"""测试：Tool 参数与结果正确、非法输入被拒（DEV_PLAN 步骤 14）。"""
import re

from src.builtin_tools import TOOL_FUNCS, TOOL_SCHEMAS, run_tool


def test_registry_contains_two_tools():
    assert set(TOOL_FUNCS) == {"calculator", "current_time"}
    names = [s["function"]["name"] for s in TOOL_SCHEMAS]
    assert set(names) == {"calculator", "current_time"}


def test_calculator_correct_results():
    assert "= 1161" in run_tool("calculator", {"expression": "27*43"})
    assert "= 37" in run_tool("calculator", {"expression": "(15+3.5)*4/2"})
    r = run_tool("calculator", {"expression": "sqrt(2)+1"})
    assert re.search(r"2\.414", r)


def test_calculator_rejects_unsafe_code():
    out = run_tool("calculator", {"expression": '__import__("os")'})
    assert "错误" in out


def test_wrong_arguments_reported_as_text():
    out = run_tool("calculator", {})  # 缺 expression
    assert "参数不对" in out


def test_unknown_tool_reported():
    out = run_tool("no_such_tool", {})
    assert "没有名为" in out


def test_current_time_format():
    out = run_tool("current_time", {})
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", out)
