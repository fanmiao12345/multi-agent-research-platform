# -*- coding: utf-8 -*-
"""D5：任务理解、能力过滤、依赖校验与版本化重规划。"""
import pytest

from src.application.orchestration import Budget, OrchestrationScheduler, PlanValidationError, from_plan_dict
from src.application.request import TaskRequest
from src.harness.planning.capabilities import default_capability_catalog
from src.harness.planning.replanner import apply_delta
from src.harness.planning.task import COMPLETED, PENDING, Plan, Task
from src.harness.planning.understanding import (load_input_request,
                                                persist_input_request,
                                                understand_task)
from tests.test_orchestration_s8 import (CAPS, _executor_with, _fanout_plan_dict,
                                         _req)


def test_task_understanding_classifies_and_persists_only_key_condition(tmp_path):
    missing = TaskRequest("写一份行业报告", flow="research")
    u1 = understand_task(missing)
    assert u1.work_type == "research_report"
    assert u1.material_gaps and not u1.needs_input

    vague = TaskRequest("比较不同方案", flow="research", texts=("资料 A",))
    u2 = understand_task(vague)
    assert u2.needs_input and u2.key_conditions == ["比较对象不明确"]
    path = persist_input_request(tmp_path / "job", u2)
    assert path.exists() and load_input_request(tmp_path / "job")["needs_input"] is True

    clear = TaskRequest("比较方案 A 与方案 B", flow="research", texts=("资料 A",))
    assert understand_task(clear).needs_input is False


def test_capability_catalog_filters_unimplemented_modes():
    decision = default_capability_catalog().available(
        has_sources=True, network_available=True,
        available_tools={"calculator"}, model_available=True, max_cost_usd=1.0)
    assert decision.available == ["single", "fixed", "manager_worker", "fanout", "dynamic_team"]
    rejected = {item["mode"]: item["reason"] for item in decision.rejected}
    assert rejected["debate"] == "尚未实现"
    assert "debate" in rejected


def test_plan_contract_rejects_dependency_cycle():
    data = _fanout_plan_dict()
    data["subtasks"] = [
        {"id": "T1", "role": "researcher", "description": "A", "depends_on": ["T2"]},
        {"id": "T2", "role": "researcher", "description": "B", "depends_on": ["T1"]},
    ]
    with pytest.raises(PlanValidationError, match="循环"):
        from_plan_dict(data)


def test_executor_blocks_task_when_dependency_did_not_complete(tmp_path):
    plan_data = _fanout_plan_dict()
    plan_data["subtasks"] = [
        {"id": "T1", "role": "researcher", "description": "子题一", "depends_on": []},
        {"id": "T2", "role": "researcher", "description": "子题二", "depends_on": ["T1"]},
        {"id": "T3", "role": "writer", "description": "成稿", "depends_on": ["T1", "T2"],
         "covers_sections": ["资料目录"]},
    ]

    def script(req):
        if "子题一" in req.task:
            return ("job_s1", "", "failed")
        if "子题二" in req.task:
            return ("job_s2", "不应执行", "accepted")
        return ("job_root", "最终报告", "draft")

    root = "job_" + "d" * 32
    ex, _, runs = _executor_with(script, workspace_root=tmp_path)
    record = ex.execute_plan(_req(), from_plan_dict(plan_data), budget_caps=CAPS,
                             root_job_id=root)
    by_id = {item["id"]: item for item in record["subtasks"]}
    assert by_id["T1"]["failure"]
    assert "前置依赖未完成" in by_id["T2"]["failure"]
    assert not any("子题二" in run["task"] for run in runs)


def test_replan_versions_invalidates_downstream_but_keeps_other_results():
    plan = Plan(goal="g", tasks=[
        Task(id="T1", description="A", status=COMPLETED, result="a"),
        Task(id="T2", description="B", status=COMPLETED, result="b"),
        Task(id="T3", description="C", status=COMPLETED, result="c", depends_on=["T2"]),
    ])
    new = apply_delta(plan, {"modify_tasks": [{"id": "T2", "description": "B2"}]})
    assert new.version == 2 and new.parent_version == 1
    assert new.by_id("T1").status == COMPLETED and new.by_id("T1").result == "a"
    assert new.by_id("T2").status == PENDING
    assert new.by_id("T3").status == PENDING and "失效" in new.by_id("T3").error
    assert {"T2", "T3"}.issubset(set(new.invalidated_task_ids))


def test_scheduler_metadata_contains_understanding_and_catalog():
    request = TaskRequest("整理资料目录", flow="research", texts=("材料",))
    understanding = understand_task(request)
    plan, meta = OrchestrationScheduler(None).plan(
        request.task, required_sections=(), budget_caps=Budget(20, 1.0, 100),
        allowed_modes=("fixed", "fanout"), understanding=understanding)
    assert plan.mode == "fixed"
    assert meta["understanding"]["work_type"] == "collection"
    assert any(item["name"] == "debate" for item in meta["capability_catalog"]["modes"])

def test_executor_detects_no_progress_replan(monkeypatch):
    from src.harness.planning import executor as planning_executor
    from src.llm.mock import MockLLM

    plan = Plan(goal="g", tasks=[Task(id="T1", description="总是失败")])
    monkeypatch.setattr(planning_executor, "replan",
                        lambda llm, current, failures: Plan.from_dict(current.to_dict()))
    result = planning_executor.execute_plan(
        MockLLM(), plan, runner=lambda task: (_ for _ in ()).throw(RuntimeError("失败")))
    assert result.no_progress is True and not result.success
