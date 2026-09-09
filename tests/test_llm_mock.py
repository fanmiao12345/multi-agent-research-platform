# -*- coding: utf-8 -*-
"""测试：MockLLM 可运行且规则正确（DEV_PLAN 步骤 14）。"""
from src.llm.mock import MockLLM


def _ask(text: str):
    return MockLLM().chat([{"role": "user", "content": text}],
                          tools=[{"type": "function"}])


def test_time_trigger_requests_tool():
    r = _ask("现在几点了？")
    assert r.wants_tool
    assert r.tool_calls[0].name == "current_time"


def test_expression_extraction():
    r = _ask("帮我计算 27*43")
    assert r.tool_calls[0].name == "calculator"
    assert r.tool_calls[0].arguments["expression"] == "27*43"


def test_plain_question_returns_text():
    r = _ask("你好呀")
    assert not r.wants_tool
    assert "你好呀" in (r.content or "")


def test_chinese_operator_translated():
    r = _ask("6 乘以 7 等于多少")
    assert r.tool_calls[0].arguments["expression"] == "6 * 7"
