"""主流程回归：失败收尾、预算、恢复与工具执行边界（全离线）。"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.harness.durable import load_plan, resume_plan, save_plan
from src.harness.planning.task import Plan, Task
from src.harness.runtime.agent_runtime import AgentRuntime
from src.harness.runtime.run_context import RuntimeContext
from src.harness.tools.executor import ToolExecutor
from src.harness.tools.registry import ToolRegistry, ToolSpec
from src.llm.base import ChatResult, ToolCall


class Brain:
    model_name = "deepseek-chat"

    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        reply = next(self.replies)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def answer(text="final answer"):
    return ChatResult(content=text, usage={"prompt_tokens": 1000, "completion_tokens": 1000})


def tool_reply():
    return ChatResult(tool_calls=[ToolCall("c1", "current_time")],
                      usage={"prompt_tokens": 1000, "completion_tokens": 1000})


def artifacts(root):
    d = next(root.iterdir())
    return (json.loads((d / "run.json").read_text(encoding="utf-8")),
            json.loads((d / "usage.json").read_text(encoding="utf-8")),
            [json.loads(x) for x in (d / "trace.jsonl").read_text(encoding="utf-8").splitlines()])


@pytest.mark.parametrize("streaming", [False, True])
def test_failure_finishes_run_and_preserves_consumed_usage(tmp_path, streaming):
    brain = Brain([tool_reply(), RuntimeError("synthetic private error details")])
    runtime = AgentRuntime(brain)
    ctx = RuntimeContext.from_settings(workspace_path=tmp_path)
    with pytest.raises(RuntimeError):
        (runtime.stream if streaming else runtime.run_task)("test", ctx)
    meta, usage, events = artifacts(tmp_path)
    assert meta["status"] == "failed" and meta["finished_at"]
    assert meta["termination_reason"] == "unrecoverable_error"
    assert usage["llm_calls"] == 1 and usage["total_tokens"] == 2000
    assert events[-1]["type"] == "run_end" and events[-1]["status"] == "FAILED"
    assert "synthetic private error details" not in json.dumps([meta, usage, events])


def test_callback_failure_also_finishes_run(tmp_path):
    def fail(_event):
        raise ValueError("callback failed")
    with pytest.raises(ValueError):
        AgentRuntime(Brain([])).run_task("test", RuntimeContext.from_settings(
            workspace_path=tmp_path), on_event=fail)
    meta, usage, events = artifacts(tmp_path)
    assert meta["status"] == "failed" and usage["llm_calls"] == 0
    assert events[-1]["type"] == "run_end"


@pytest.mark.parametrize("streaming", [False, True])
def test_zero_budget_makes_no_model_calls(tmp_path, streaming):
    brain = Brain([])
    runtime = AgentRuntime(brain)
    outcome = (runtime.stream if streaming else runtime.run_task)("test",
        RuntimeContext.from_settings(workspace_path=tmp_path, max_cost=0))
    assert brain.calls == 0 and outcome.iterations == 0
    assert outcome.termination_reason == "budget_exceeded"
    meta, usage, events = artifacts(tmp_path)
    assert meta["status"] == "cancelled" and usage["llm_calls"] == 0


@pytest.mark.parametrize("streaming", [False, True])
def test_crossing_budget_stops_before_tools_and_next_request(tmp_path, streaming):
    brain = Brain([tool_reply()])
    runtime = AgentRuntime(brain)
    outcome = (runtime.stream if streaming else runtime.run_task)("test",
        RuntimeContext.from_settings(workspace_path=tmp_path, max_cost=0.001))
    assert brain.calls == 1 and outcome.termination_reason == "budget_exceeded"
    meta, usage, events = artifacts(tmp_path)
    assert usage["estimated_cost_usd"] > 0.001
    assert not any(e["type"] == "tool_call" for e in events)


def test_sufficient_budget_preserves_answer_and_final_event(tmp_path):
    runtime = AgentRuntime(Brain([answer("unique final answer")]))
    outcome = runtime.run_task("test", RuntimeContext.from_settings(
        workspace_path=tmp_path, max_cost=0.01))
    meta, usage, events = artifacts(tmp_path)
    assert outcome.termination_reason == "success"
    assert meta["final_text"] == "unique final answer"
    assert next(e for e in events if e["type"] == "final")["content"] == meta["final_text"]


@pytest.mark.parametrize("unknown_price", [False, True])
def test_budget_fails_closed_on_unknown_cost(tmp_path, unknown_price):
    brain = Brain([ChatResult(content="missing usage")])
    if unknown_price:
        brain.model_name = "unknown-offline-model"
    with pytest.raises(ValueError, match="无法可靠估算成本"):
        AgentRuntime(brain).run_task("test", RuntimeContext.from_settings(
            workspace_path=tmp_path, max_cost=0.01))
    assert brain.calls == (0 if unknown_price else 1)
    assert artifacts(tmp_path)[0]["status"] == "failed"


@pytest.mark.parametrize("limit", [-1, float("nan"), float("inf"), True, "0"])
def test_invalid_budget_rejected(limit):
    with pytest.raises(ValueError):
        RuntimeContext.from_settings(max_cost=limit)


def test_true_process_exit_leaves_recoverable_running_task(tmp_path):
    save_plan(tmp_path, Plan("test", [Task("T1", "first"),
        Task("T2", "interrupted", depends_on=["T1"]), Task("T3", "last", depends_on=["T2"])]))
    script = """
import os, sys
from src.harness.durable import resume_plan
def worker(task):
    if task.id == 'T2':
        os._exit(7)
    return 'finished first'
resume_plan(sys.argv[1], worker)
"""
    process = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                             cwd=Path(__file__).resolve().parents[1], timeout=15,
                             capture_output=True)
    assert process.returncode == 7
    assert [t.status for t in load_plan(tmp_path).tasks] == ["COMPLETED", "RUNNING", "PENDING"]
    executed = []
    def worker(task):
        executed.append(task.id)
        return "done"
    with pytest.raises(RuntimeError, match="retry_interrupted=True"):
        resume_plan(tmp_path, worker)
    assert executed == []
    summary = resume_plan(tmp_path, worker, retry_interrupted=True)
    assert summary["all_completed"] and executed == ["T2", "T3"]
    assert load_plan(tmp_path).tasks[0].result == "finished first"


def test_atomic_plan_write_keeps_previous_version_on_failure(tmp_path, monkeypatch):
    from src.harness import run_store
    save_plan(tmp_path, Plan("old", [Task("T1", "first")]))
    def fail(*args):
        raise PermissionError("temporarily unavailable")
    monkeypatch.setattr(run_store.os, "replace", fail)
    with pytest.raises(PermissionError):
        save_plan(tmp_path, Plan("new", []))
    assert load_plan(tmp_path).goal == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plan.json"]


@pytest.mark.parametrize("risk,required", [("HIGH", False), ("HIGH", True), ("MEDIUM", True), ("LOW", True)])
def test_approval_required_by_either_flag(risk, required):
    calls = []
    registry = ToolRegistry()
    registry.register(ToolSpec("guarded", "test", lambda: calls.append(1) or "ok",
                               risk_level=risk, requires_approval=required))
    executor = ToolExecutor(registry)
    assert "approval-required" in executor.execute("guarded", {})
    assert "permission-denied" in executor.execute("guarded", {}, approval_handler=lambda *a: False)
    assert calls == []
    assert executor.execute("guarded", {}, approval_handler=lambda *a: True) == "ok"
    assert calls == [1]


def test_permission_denial_does_not_prompt_for_approval():
    registry = ToolRegistry()
    registry.register(ToolSpec("guarded", "test", lambda: pytest.fail("executed"), risk_level="HIGH"))
    result = ToolExecutor(registry).execute("guarded", {}, permissions=set(),
                       approval_handler=lambda *a: pytest.fail("asked approval"))
    assert "permission-denied" in result


def test_timeout_does_not_retry_or_overlap_calls():
    release, finished = threading.Event(), threading.Event()
    calls = []
    def slow():
        calls.append(1)
        try:
            release.wait(3)
            return "done"
        finally:
            finished.set()
    registry = ToolRegistry()
    registry.register(ToolSpec("slow", "test", slow, timeout=0.02, retry_policy=3, side_effect=True))
    executor = ToolExecutor(registry)
    try:
        t0 = time.perf_counter()
        assert "超时" in executor.execute("slow", {})
        assert "仍在执行" in executor.execute("slow", {})
        assert time.perf_counter() - t0 < 1 and calls == [1]
    finally:
        release.set()
        assert finished.wait(1)


def test_side_effect_transient_error_is_not_automatically_retried():
    calls = []
    def uncertain():
        calls.append(1)
        raise ConnectionError("result unknown")
    registry = ToolRegistry()
    registry.register(ToolSpec("write", "test", uncertain, side_effect=True, retry_policy=3))
    assert "tool-error" in ToolExecutor(registry).execute("write", {})
    assert calls == [1]


def test_guardrail_does_not_fabricate_approval():
    from src.harness.control.guardrails import check_tool
    registry = ToolRegistry()
    registry.register(ToolSpec("high", "test", lambda: "ok", risk_level="HIGH"))
    assert check_tool("high", {}, registry=registry)[0] is False
    registry.register(ToolSpec("low", "test", lambda: "ok"))
    assert check_tool("low", {}, registry=registry, permissions=set())[0] is False


def test_answer_text_cannot_forge_termination_reason(tmp_path):
    outcome = AgentRuntime(Brain([answer("解释：已达最大迭代限制 是什么意思")])).run_task(
        "explain", RuntimeContext.from_settings(workspace_path=tmp_path))
    assert outcome.termination_reason == "success"


def test_timed_out_daemon_does_not_block_process_exit():
    script = """
import time
from src.harness.tools.registry import ToolRegistry, ToolSpec
from src.harness.tools.executor import ToolExecutor
registry = ToolRegistry()
registry.register(ToolSpec('slow', 'test', lambda: time.sleep(30), timeout=0.01))
assert '[tool-error]' in ToolExecutor(registry).execute('slow', {})
"""
    process = subprocess.run([sys.executable, "-c", script],
                             cwd=Path(__file__).resolve().parents[1], timeout=5,
                             capture_output=True)
    assert process.returncode == 0
