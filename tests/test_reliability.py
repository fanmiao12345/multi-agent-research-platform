# -*- coding: utf-8 -*-
"""测试：Reliability / Durable / HITL（M8 步骤 84-96）。"""
import os
import shutil
import uuid

import pytest

from src.harness.control.errors import classify, is_retryable
from src.harness.control.guardrails import (check_input, check_output,
                                            check_tool)
from src.harness.control.hitl import (HitlDecision, Interrupt, run_guarded)
from src.harness.control.idempotency import ExecutionLedger
from src.harness.control.loop_guard import LoopGuardConfig, analyze
from src.harness.control.policies import RetryPolicy, TimeoutPolicy, exceeded
from src.harness.durable import resume_plan, save_plan
from src.harness.planning.task import Plan, Task
from src.harness.state_review import edit_plan_task, resume_run, review_run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def run_dir():
    d = os.path.join(ROOT, "workspaces", "_t_durable_" + uuid.uuid4().hex[:6])
    os.makedirs(d, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _plan3():
    return Plan(goal="g", tasks=[
        Task(id="T1", description="一"),
        Task(id="T2", description="二", depends_on=["T1"]),
        Task(id="T3", description="三", depends_on=["T2"]),
    ])


# ---------- 86 错误分类 ----------
def test_error_classification():
    assert classify(ConnectionError("boom")) == "transient"
    assert classify(TimeoutError("x")) == "timeout"
    assert classify(ValueError("bad")) == "validation"
    assert classify(PermissionError("no")) == "permission"
    assert classify("429 rate limit") == "rate_limit"
    assert classify(RuntimeError("其他")) == "permanent"
    assert is_retryable("transient") and not is_retryable("validation")


# ---------- 87/88 Retry & Timeout Policy ----------
def test_retry_policy():
    p = RetryPolicy(max_attempts=3)
    assert p.should_retry("transient", attempt=0)
    assert p.should_retry("timeout", attempt=1)
    assert not p.should_retry("validation", attempt=0)
    assert not p.should_retry("transient", attempt=2)
    zero = RetryPolicy.from_int(0)
    assert not zero.should_retry("transient", 0)


def test_timeout_policy():
    import time as _t
    tp = TimeoutPolicy(run=0.05)
    started = _t.perf_counter()
    assert not exceeded(started, None)
    assert not exceeded(started, tp.run)
    _t.sleep(0.06)
    assert exceeded(started, tp.run)


# ---------- 85 Idempotency ----------
def test_idempotency_ledger(run_dir):
    calls = {"n": 0}

    def side_effect(content: str) -> str:
        calls["n"] += 1
        return f"已写入:{content}"

    ledger = ExecutionLedger(os.path.join(run_dir, "ledger.json"))
    r1, m1 = ledger.execute_once("run1", "write_file", {"content": "x"}, side_effect)
    r2, m2 = ledger.execute_once("run1", "write_file", {"content": "x"}, side_effect)
    assert m1 == "executed" and m2 == "replayed"
    assert r1 == r2 and calls["n"] == 1          # 副作用只发生一次
    # 不同参数 / 不同 run 各自执行
    ledger.execute_once("run1", "write_file", {"content": "y"}, side_effect)
    assert calls["n"] == 2


# ---------- 89 Loop Guard ----------
def test_loop_guard_detects_patterns():
    same = [{"type": "tool_call", "name": "calc", "arguments": {"e": "1+1"}}] * 3
    assert analyze(same)["flags"]["same_tool_loop"]
    assert analyze(same)["stop"]

    no_prog = [{"type": "llm_call"}] * LoopGuardConfig().max_no_progress_rounds
    assert analyze(no_prog)["flags"]["no_progress"]

    rework = [{"type": "verdict", "value": "rework"}] * 3
    assert analyze(rework)["flags"]["rework_loop"]

    handoff = ([{"type": "handoff", "from": "A", "to": "B"},
                {"type": "handoff", "from": "B", "to": "A"}] * 3)
    assert analyze(handoff)["flags"]["handoff_cycle"]

    clean = [{"type": "message_added"}] * 2
    assert analyze(clean, iteration=1)["stop"] is False


# ---------- 90-92 Guardrails ----------
def test_guardrails_three_layers():
    assert check_input("正常问题") == []
    assert check_input("x" * 9000, max_len=100) != []
    assert check_input("带禁词的内容", forbid=("禁词",)) != []

    ok, _ = check_tool("calculator", {"expression": "1+1"})
    assert ok
    ok, _ = check_tool("calculator", {"expression": 1}, deny=())
    assert not ok  # 类型错
    ok, _ = check_tool("nope", {})
    assert not ok

    assert check_output("回答") == []
    assert check_output("短", min_len=5) != []
    assert check_output("含禁词", forbid=("禁词",)) != []


# ---------- 93-95 HITL ----------
def test_hitl_approval_and_edit():
    calls = []

    def execute(new_value=None):
        calls.append(new_value)
        return f"执行了:{new_value}"

    def auto_approve(_i):
        return HitlDecision("approve")

    out = run_guarded(Interrupt("approval", "删除文件", "del x"),
                      lambda: execute(), auto_approve)
    assert out == "执行了:None" and len(calls) == 1

    def auto_reject(_i):
        return HitlDecision("reject")

    out2 = run_guarded(Interrupt("approval", "删除文件"), lambda: execute(),
                       auto_reject)
    assert "拒绝" in out2 and len(calls) == 1      # 未执行

    def auto_edit(_i):
        return HitlDecision("edit", "新参数")

    out3 = run_guarded(Interrupt("edit", "改参数"), execute,
                       lambda i: auto_edit(i))
    assert calls[-1] == "新参数"


# ---------- 84/96 故障实验：进程中断 → Resume ----------
def _stub_worker(executed):
    def worker(task):
        executed.append(task.id)
        return f"done-{task.id}"
    return worker


def test_experiment_resume_after_crash(run_dir):
    plan = _plan3()
    save_plan(run_dir, plan)

    # 阶段一：执行到 T2 时"进程崩溃"（抛异常中断，不继续）
    executed1 = []
    with pytest.raises(RuntimeError):
        resume_plan(run_dir, _crash_worker(executed1, crash_at="T2"))

    # 阶段二：新"进程"恢复 → 已完成的不重跑，只做剩余
    executed2 = []
    summary = resume_plan(run_dir, _stub_worker(executed2))
    assert summary["all_completed"] is True
    assert executed1 == ["T1", "T2"]      # 第一次做到 T2
    assert executed2 == ["T2", "T3"]      # 恢复后：T2 重试 + T3（T1 不重跑）
    assert summary["resumed_from_skipped"] == ["T1"]
    assert summary["completed_total"] == 3


def _crash_worker(executed, crash_at):
    def worker(task):
        executed.append(task.id)
        if task.id == crash_at:
            raise RuntimeError(f"模拟进程在 {task.id} 中断")
        return f"done-{task.id}"
    return worker


def test_state_review_edit_and_resume(run_dir):
    save_plan(run_dir, _plan3())
    view = review_run(run_dir)
    assert "plan" in view and len(view["plan"]["tasks"]) == 3
    assert edit_plan_task(run_dir, "T2", description="改过的二") is True
    assert edit_plan_task(run_dir, "T9", description="x") is False
    view2 = review_run(run_dir)
    assert view2["plan"]["tasks"][1]["description"] == "改过的二"
    summary = resume_run(run_dir, _stub_worker([]))
    assert summary["completed_total"] == 3
