# -*- coding: utf-8 -*-
"""测试：Structured Output / Tool Registry / Schema / Executor（M1 步骤 22-24）。"""
import time

import pytest

from src.harness.structured import decision_text, extract_json, parse_labels
from src.harness.tools.executor import (TOOL_APPROVAL_REQUIRED, TOOL_ERROR,
                                        TOOL_PERMISSION_DENIED, ToolExecutor)
from src.harness.tools.registry import (RISK_HIGH, RISK_LOW, ToolRegistry,
                                        ToolSpec)
from src.harness.tools.schema import validate_arguments


# ---------- 22 Structured Output ----------
def test_extract_json_fenced_and_plain():
    fenced = '前言\n```json\n{"mode": "multi", "steps": ["a"]}\n```\n后记'
    assert extract_json(fenced) == {"mode": "multi", "steps": ["a"]}
    plain = '随便说点 { "mode": "single" } 结束'
    assert extract_json(plain) == {"mode": "single"}


def test_extract_json_malformed_returns_none():
    assert extract_json("没有 JSON") is None
    assert extract_json("") is None


def test_parse_labels_and_failure():
    ok = parse_labels("MODE: multi\nREASON: 复杂\nSTEPS: a,b", ("mode", "reason", "steps"))
    assert ok == {"mode": "multi", "reason": "复杂", "steps": "a,b"}
    assert parse_labels("MODE: single", ("mode", "reason")) is None  # 缺 reason
    assert parse_labels("", ("mode",)) is None


def test_decision_text_roundtrip():
    assert decision_text("VERDICT", "pass") == "VERDICT: pass"


# ---------- 23 Registry ----------
def test_registry_builtins():
    reg = ToolRegistry.with_builtins()
    assert sorted(t.name for t in reg.list()) == ["calculator", "current_time"]
    assert reg.get("calculator").risk_level == RISK_LOW
    # search / filter
    assert [t.name for t in reg.search("math")] == ["calculator"]
    assert reg.filter(names=("current_time",))[0].name == "current_time"


def test_registry_register_unregister():
    reg = ToolRegistry()
    reg.register(ToolSpec(name="demo", description="x", func=lambda: "ok"))
    assert reg.get("demo") is not None
    reg.unregister("demo")
    assert reg.get("demo") is None


# ---------- Schema ----------
def test_schema_validation():
    spec = {"type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"]}
    assert validate_arguments(spec, {"expression": "1+1"}) is None
    assert "缺少必填参数" in validate_arguments(spec, {})
    assert "类型错误" in validate_arguments(spec, {"expression": 42})


# ---------- 24 Executor ----------
def test_executor_happy_and_errors():
    ex = ToolExecutor(ToolRegistry.with_builtins())
    assert "= 1161" in ex.execute("calculator", {"expression": "27*43"})
    assert "没有名为" in ex.execute("nope", {})
    assert TOOL_PERMISSION_DENIED in ex.execute(
        "calculator", {"expression": "1+1"}, permissions=set())
    assert TOOL_ERROR in ex.execute("calculator", {"expression": "__import__('os')"})


def test_executor_high_risk_requires_approval():
    reg = ToolRegistry()
    reg.register(ToolSpec(name="rm", description="危险删除", func=lambda: "deleted",
                          risk_level=RISK_HIGH, requires_approval=True))
    ex = ToolExecutor(reg)
    assert TOOL_APPROVAL_REQUIRED in ex.execute("rm", {})
    # 有审批者放行
    assert ex.execute("rm", {}, approver="admin") == "deleted"


def test_executor_retries_transient():
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise ConnectionError("临时网络错误")
        return "third-time-lucky"

    reg = ToolRegistry()
    reg.register(ToolSpec(name="flaky", description="f", func=flaky, retry_policy=2))
    ex = ToolExecutor(reg)
    assert ex.execute("flaky", {}) == "third-time-lucky"
    assert state["n"] == 3


def test_executor_timeout():
    import threading
    release = threading.Event()
    finished = threading.Event()

    def slow():
        try:
            release.wait(3)
            return "too late"
        finally:
            finished.set()

    reg = ToolRegistry()
    reg.register(ToolSpec(name="slow", description="s", func=slow, timeout=0.1))
    started = time.perf_counter()
    try:
        out = ToolExecutor(reg).execute("slow", {})
        assert time.perf_counter() - started < 1.0
        assert TOOL_ERROR in out and "超时" in out
        assert not finished.is_set()
    finally:
        release.set()
        assert finished.wait(1)


def test_permission_denied_matches_runtime_flow():
    # RuntimeContext 默认权限含 calculator；剔除后 Agent 循环应拿到权限拒绝文本
    from src.graph.agent_loop import build_agent_graph
    from src.llm.base import ChatResult, LLMAdapter, ToolCall

    class OneShotStub(LLMAdapter):
        model_name = "stub-one"

        def chat(self, messages, tools=None):
            return ChatResult(tool_calls=[
                ToolCall(id="t1", name="calculator", arguments={"expression": "1+1"})])

    app = build_agent_graph(OneShotStub(), max_iterations=2, permissions=frozenset())
    result = app.invoke({"messages": [{"role": "user", "content": "算一下"}],
                         "iteration": 0, "max_iterations": 2})
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    assert tool_msgs and TOOL_PERMISSION_DENIED in tool_msgs[0]["content"]
