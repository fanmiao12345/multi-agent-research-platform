# -*- coding: utf-8 -*-
"""测试：Trace Event Schema / Usage 统计 / Streaming（M1 步骤 28-30）。"""
import json
import os
import shutil

import pytest

from src.harness.runtime.agent_runtime import AgentRuntime
from src.harness.runtime.run_context import RuntimeContext
from src.harness.tracer import (EV_LLM_CALL, EV_RUN_END, TRACE_FIELD_SCHEMA,
                                Tracer)
from src.harness.usage import UsageTracker, estimate_cost_usd
from src.llm.base import ChatResult, LLMAdapter, ToolCall
from src.llm.mock import MockLLM


class UsageStub(LLMAdapter):
    """带 usage 的收敛大脑：一次工具后收尾。"""
    model_name = "deepseek-chat"

    def chat(self, messages, tools=None):
        if any(m.get("role") == "tool" for m in messages):
            return ChatResult(content="1161", usage={"prompt_tokens": 20,
                                                     "completion_tokens": 5})
        return ChatResult(tool_calls=[ToolCall(id="t1", name="calculator",
                                               arguments={"expression": "27*43"})],
                          usage={"prompt_tokens": 10, "completion_tokens": 4})


@pytest.fixture()
def ws(tmp_path_factory):
    out = tmp_path_factory.mktemp("m1_ws") / "workspaces"
    yield out
    shutil.rmtree(out, ignore_errors=True)


# ---------- 28 Trace Event Schema ----------
def test_tracer_full_schema(tmp_path):
    t = Tracer(tmp_path, run_id="R1", thread_id="T1", model="m1")
    with t.timed_event(EV_LLM_CALL, node="agent", agent="demo") as ev:
        ev["input_tokens"] = 3
    t.event(EV_RUN_END, node="runtime", status="COMPLETED", reason="success")
    lines = [json.loads(x) for x in t.path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    for ev in lines:
        for field in TRACE_FIELD_SCHEMA:
            assert field in ev, f"缺字段 {field}"
    assert lines[0]["run_id"] == "R1" and lines[0]["thread_id"] == "T1"
    assert lines[0]["latency"] is not None


# ---------- 29 Usage ----------
def test_usage_tracker_and_cost():
    tr = UsageTracker(model="deepseek-chat")
    tr.record("deepseek-chat", {"prompt_tokens": 100, "completion_tokens": 50}, 0.5)
    tr.record("deepseek-chat", {"prompt_tokens": 200, "completion_tokens": 60}, 0.7)
    s = tr.summary("R1")
    assert s["llm_calls"] == 2
    assert s["total_tokens"] == 410
    assert s["estimated_cost_usd"] is not None
    assert estimate_cost_usd("unknown-model", 1, 1) is None


# ---------- 29+28 端到端：runtime 落 usage.json / trace.jsonl ----------
def test_runtime_writes_usage_and_trace(ws):
    ctx = RuntimeContext.from_settings(max_iterations=3, workspace_path=ws)
    outcome = AgentRuntime(UsageStub()).run_task("算 27*43", context=ctx)
    usage = json.load(open(os.path.join(outcome.workspace_dir, "usage.json"),
                           encoding="utf-8"))
    assert usage["llm_calls"] == 2
    assert usage["total_tokens"] == 39
    trace_lines = open(os.path.join(outcome.workspace_dir, "trace.jsonl"),
                       encoding="utf-8").read().splitlines()
    kinds = {json.loads(x)["type"] for x in trace_lines}
    assert {"llm_call", "tool_call", "tool_result", "run_end"} <= kinds
    # llm_call 事件带 latency 与 tokens
    llm_ev = [json.loads(x) for x in trace_lines if json.loads(x)["type"] == "llm_call"][0]
    assert llm_ev["latency"] is not None
    assert llm_ev["input_tokens"] is not None


# ---------- 30 Streaming ----------
def test_stream_emits_events_and_matches_invoke(ws):
    ctx = RuntimeContext.from_settings(max_iterations=3, workspace_path=ws)
    events = []
    runtime = AgentRuntime(UsageStub())
    stream_out = runtime.stream("算 27*43", context=ctx, on_event=events.append)
    kinds = {e["type"] for e in events}
    assert {"run_start", "llm", "tool", "node_end", "final"} <= kinds
    assert stream_out.final_text == "1161"
    # stream 与 invoke 结果一致（同一工作区再跑一次 run）
    invoke_out = runtime.run_task("算 27*43", context=ctx)
    assert invoke_out.final_text == stream_out.final_text
    assert invoke_out.iterations == stream_out.iterations
