# -*- coding: utf-8 -*-
"""测试：Planning（M3 步骤 37-44）。"""
import pytest

from src.harness.planning.executor import execute_plan, plan_and_execute
from src.harness.planning.planner import plan_task
from src.harness.planning.replanner import apply_delta, replan
from src.harness.planning.scheduler import Scheduler
from src.harness.planning.task import (COMPLETED, FAILED, PENDING, Plan, Task)
from src.harness.planning.task_graph import TaskGraph
from src.llm.mock import MockLLM


# ---------- 38 Task 状态机 ----------
def test_task_lifecycle_and_illegal_jump():
    t = Task(id="T1", description="x")
    t.transition("READY").transition("RUNNING").transition(COMPLETED)
    assert t.status == COMPLETED
    with pytest.raises(ValueError):
        Task(id="T2", description="y").transition(COMPLETED)  # PENDING 直跳非法
    # FAILED 可被 reset 重试（必须先 RUNNING 再失败，路径合法）
    f = Task(id="T3", description="z").transition("RUNNING").transition(FAILED)
    f.reset()
    assert f.status == PENDING and f.attempts == 1


def test_plan_llm_json_validation():
    bad = {"goal": "g", "tasks": [{"id": "T1", "description": "a",
                                   "depends_on": ["T9"]}]}  # 依赖不存在
    plan = Plan.from_llm_json(bad, fallback_task="兜底")
    assert len(plan.tasks) == 1 and plan.tasks[0].id == "T1"
    assert plan.tasks[0].description == "兜底"  # 校验失败 → 单任务兜底


# ---------- 39 Task Graph 就绪/阻塞 ----------
def _diamond():
    return Plan(goal="g", tasks=[
        Task(id="T1", description="查资料", preferred_agent="researcher"),
        Task(id="T2", description="整理", depends_on=["T1"]),
        Task(id="T3", description="校验", depends_on=["T1"]),
        Task(id="T4", description="合并", depends_on=["T2", "T3"]),
        Task(id="T5", description="成稿", depends_on=["T4"]),
    ])


def test_graph_ready_order_and_blocking():
    plan = _diamond()
    g = TaskGraph(plan)
    assert [t.id for t in g.ready_tasks()] == ["T1"]
    plan.by_id("T1").transition("RUNNING").transition(COMPLETED)
    assert sorted(t.id for t in g.ready_tasks()) == ["T2", "T3"]  # 并行就绪
    plan.by_id("T2").transition("RUNNING").transition(FAILED)
    g.mark_blocked_cascade()
    assert plan.by_id("T4").status == "BLOCKED"
    assert [t.id for t in g.blocked_tasks()] == []  # blocked 已是 BLOCKED 态


# ---------- 40 Scheduler ----------
def test_scheduler_deterministic():
    plan = _diamond()
    s = Scheduler(TaskGraph(plan))
    assert s.next_one().id == "T1"
    snap = s.snapshot()
    assert snap["counts"]["PENDING"] == 5


# ---------- 37 Planner（Mock 启发式）----------
def test_planner_mock_chain_and_single():
    llm = MockLLM()
    chain = plan_task(llm, "帮我调研 X 并写一篇报告")
    assert [t.id for t in chain.tasks] == ["T1", "T2", "T3"]
    assert chain.tasks[1].depends_on == ["T1"]
    single = plan_task(llm, "现在几点？")
    assert len(single.tasks) == 1


# ---------- 43 apply_delta 纯函数 ----------
def test_apply_delta_pure():
    plan = _diamond()
    plan.by_id("T2").transition("RUNNING").transition(FAILED)
    new = apply_delta(plan, {"modify_tasks": [{"id": "T2", "description": "换法重试"}]})
    assert new is not plan
    assert new.by_id("T2").status == PENDING
    assert new.by_id("T2").description == "换法重试"
    # 原始 plan 不受影响（纯函数）
    assert plan.by_id("T2").status == FAILED


# ---------- 41/42/44 执行器：失败→重规划→成功 ----------
def test_executor_replan_recovers():
    plan = _diamond()
    fail_once = {"T3": False}

    def runner(task):
        if task.id == "T3" and not fail_once["T3"]:
            fail_once["T3"] = True
            raise RuntimeError("T3 模拟第一次失败")
        return f"done-{task.id}"

    result = execute_plan(MockLLM(), plan, runner=runner, max_replans=2)
    assert result.success
    assert result.replans == 1
    assert result.executed == 6  # 5 步 + 1 次重试
    m = result.metrics()
    assert m["plan_completion_rate"] == 1.0
    assert m["replan_count"] == 1 and m["replan_success"] is True


def test_executor_persistent_failure_degrades():
    def always_fail(task):
        raise RuntimeError("永远失败")

    plan = _diamond()
    result = execute_plan(MockLLM(), plan, runner=always_fail, max_replans=2,
                          max_attempts_per_task=2)
    assert not result.success
    m = result.metrics()
    assert m["completed"] < m["task_count"]


def test_plan_and_execute_mock_chain():
    result = plan_and_execute(MockLLM(), "帮我调研远程办公并写一篇报告",
                              runner=lambda t: "结果-" + t.id)
    assert result.success
    assert len(result.plan.tasks) == 3
