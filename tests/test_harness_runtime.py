# -*- coding: utf-8 -*-
"""测试：Lifecycle / RuntimeContext / Termination / AgentRuntime（M1 步骤 17-21）。"""
import os
import shutil

import pytest

from config.settings import Settings
from src.graph.state import new_state
from src.harness.runtime import termination
from src.harness.runtime.agent_runtime import AgentRuntime
from src.harness.runtime.lifecycle import (CANCELLED, COMPLETED, CREATED,
                                           FAILED, RUNNING, RunRecord)
from src.harness.runtime.run_context import RuntimeContext
from src.llm.base import ChatResult, LLMAdapter, ToolCall
from src.llm.mock import MockLLM


# ---------- 18 Lifecycle ----------
def test_lifecycle_happy_path():
    r = RunRecord(run_id="r1").transition(RUNNING).transition(COMPLETED, "success")
    assert r.status == COMPLETED
    assert r.termination_reason == "success"
    assert r.ended_at


def test_lifecycle_rejects_illegal_jump():
    r = RunRecord(run_id="r1")  # CREATED
    with pytest.raises(ValueError):
        r.transition(COMPLETED)  # CREATED 不能直接到 COMPLETED


def test_terminal_state_locked():
    r = RunRecord(run_id="r1").transition(RUNNING).transition(FAILED, "unrecoverable_error")
    with pytest.raises(ValueError):
        r.transition(CANCELLED)


# ---------- 19 RuntimeContext ----------
def test_context_defaults_and_overrides():
    ctx = RuntimeContext.from_settings()
    assert ctx.max_iterations == 5
    assert "calculator" in ctx.permissions
    ctx2 = ctx.with_updates(max_iterations=9, max_cost=0.1)
    assert ctx2.max_iterations == 9 and ctx2.max_cost == 0.1
    assert ctx.max_iterations == 5  # 原对象不变


# ---------- 20/21 State ----------
def test_new_state_shape():
    s = new_state("你好", run_id="r9", max_iterations=3)
    assert s["run_id"] == "r9"
    assert s["iteration"] == 0
    assert s["max_iterations"] == 3
    assert s["messages"] == [{"role": "user", "content": "你好"}]


def test_termination_reason_helper():
    assert termination.reason_from_timeout_note("普通回答") == termination.SUCCESS
    assert (termination.reason_from_timeout_note("（已达最大迭代限制 5 轮…）")
            == termination.MAX_ITERATIONS)


# ---------- 17 AgentRuntime ----------
class ConvergingStub(LLMAdapter):
    model_name = "stub-converge"

    def chat(self, messages, tools=None):
        if any(m.get("role") == "tool" for m in messages):
            return ChatResult(content="答案是 1161")
        return ChatResult(tool_calls=[ToolCall(id="t1", name="calculator",
                                               arguments={"expression": "27*43"})])


@pytest.fixture()
def tmp_workspace(tmp_path_factory):
    out = tmp_path_factory.mktemp("rt_ws") / "workspaces"
    yield out
    shutil.rmtree(out, ignore_errors=True)


def test_runtime_loop_guard_with_neverending_stub(tmp_workspace):
    class AlwaysTool(LLMAdapter):
        model_name = "stub-always"

        def chat(self, messages, tools=None):
            return ChatResult(tool_calls=[
                ToolCall(id="x", name="calculator", arguments={"expression": "1+1"})])

    ctx = RuntimeContext.from_settings(max_iterations=3, workspace_path=tmp_workspace)
    outcome = AgentRuntime(AlwaysTool()).run_task("帮我算一下", context=ctx)
    assert outcome.status == COMPLETED
    assert outcome.termination_reason == termination.MAX_ITERATIONS
    assert outcome.iterations == 3
    assert "已达最大迭代限制" in outcome.final_text
    # run.json 与 trace.jsonl 都在
    for name in ("run.json", "trace.jsonl"):
        assert os.path.exists(os.path.join(outcome.workspace_dir, name))


def test_runtime_mock_converges(tmp_workspace):
    ctx = RuntimeContext.from_settings(max_iterations=5, workspace_path=tmp_workspace)
    outcome = AgentRuntime(MockLLM()).run_task("帮我计算 27*43", context=ctx)
    assert outcome.termination_reason == termination.SUCCESS
    assert "1161" in outcome.final_text
    assert outcome.iterations == 2


def test_runtime_converges_with_stub(tmp_workspace):
    ctx = RuntimeContext.from_settings(max_iterations=5, workspace_path=tmp_workspace)
    outcome = AgentRuntime(ConvergingStub()).run_task("计算 27*43 并回答", context=ctx)
    assert outcome.termination_reason == termination.SUCCESS
    assert outcome.final_text == "答案是 1161"
    assert outcome.iterations == 2


def test_settings_mock_provider_detection(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    assert Settings().is_mock is True
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
