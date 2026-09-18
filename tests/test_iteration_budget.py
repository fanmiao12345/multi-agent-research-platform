# -*- coding: utf-8 -*-
"""IterationBudget：预留/结算/耗尽 + 熔断（open/half_open）+ 子智能体接线。"""
import pytest

from src.harness.budget_control import BudgetExhausted, CircuitOpen, IterationBudget


def test_reserve_consume_and_exhaust():
    b = IterationBudget(max_iterations=3)
    assert b.check() and b.remaining == 3
    b.reserve(2)
    assert b.remaining == 1
    b.consume(1)
    assert b.remaining == 0 and b.exhausted
    assert b.check() is False
    with pytest.raises(BudgetExhausted):
        b.consume(1)                      # 耗尽后再 consume：拒绝继续迭代


def test_release_returns_reserved():
    b = IterationBudget(max_iterations=2)
    b.reserve(2)
    b.release(1)
    assert b.remaining == 1


def test_circuit_breaker_states():
    b = IterationBudget(max_iterations=10, max_consecutive_failures=2)
    b.record_failure()
    assert b.state == "closed"
    b.record_success()
    assert b.failures == 0                          # 成功清零连击
    b.record_failure()
    b.record_failure()
    assert b.state == "open"                        # 连击达到阈值 → 熔断
    assert b.check() is False
    with pytest.raises(CircuitOpen):
        b.consume(1)
    assert b.try_reclose() is True and b.state == "half_open"
    assert b.check() is True                        # 试探步放行
    b.record_success()
    assert b.state == "closed"                      # 试探成功恢复闭合
    assert b.snapshot()["total_failures"] == 3


def test_half_open_failure_reopens():
    b = IterationBudget(max_iterations=10, max_consecutive_failures=1)
    b.record_failure()
    assert b.state == "open"
    b.try_reclose()
    b.record_failure()
    assert b.state == "open"                        # 试探再失败继续熔断


# ---- 子智能体工具接线：连续失败 → 熔断 → 拒绝派生 ----

class _FlakyOutcome:
    def __init__(self, reason):
        self.final_text = "x"
        self.status = "failed"
        self.termination_reason = reason


class _FlakyRuntime:
    def __init__(self):
        self.calls = 0

    def run_task(self, task, **kwargs):
        self.calls += 1
        return _FlakyOutcome("unrecoverable_error")


def test_delegate_circuit_opens_after_consecutive_failures():
    from src.harness.tools.subagent import (build_delegate_spec,
                                            subagent_permission_scope)

    runtime = _FlakyRuntime()
    fn = build_delegate_spec(runtime).func

    # 同一权限作用域内共享父级熔断器（跨调用持续记账）
    with subagent_permission_scope(frozenset()):
        results = [fn("researcher", f"子任务{i}") for i in range(4)]
    assert runtime.calls == 3                       # 前三次真实执行（默认连击=3）
    assert results[3].startswith("[circuit-open]")  # 第四次被熔断拒绝


def test_delegate_success_resets_failure_streak():
    from src.harness.tools.subagent import (build_delegate_spec,
                                            subagent_permission_scope)

    class OkThenFail:
        def __init__(self):
            self.n = 0

        def run_task(self, task, **kwargs):
            self.n += 1
            return _FlakyOutcome("unrecoverable_error" if self.n % 2 == 0
                                 else "success")

    rt = OkThenFail()
    fn = build_delegate_spec(rt).func
    with subagent_permission_scope(frozenset()):
        out1 = fn("researcher", "任务一")   # success
        out2 = fn("researcher", "任务二")   # fail（连击=1 < 3 不熔断）
        out3 = fn("researcher", "任务三")   # success（连击清零）
    assert "success" in out1 and "success" in out3
    assert "unrecoverable_error" in out2
