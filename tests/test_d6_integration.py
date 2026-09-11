# -*- coding: utf-8 -*-
"""D6-01～03：single、fixed、manager_worker 统一执行接线。"""
from src.application.orchestration import (FIRST_VERSION_MODES,
                                           OrchestrationExecutor,
                                           heuristic_plan, from_plan_dict)
from src.application.request import TaskRequest
from src.harness.planning.capabilities import default_capability_catalog
from tests.test_orchestration_s8 import CAPS, _executor_with, _req


def test_single_mode_runs_one_root_task(tmp_path):
    req = TaskRequest("计算 6*7", mode="mock", flow="agent", max_cost=0.30,
                      max_seconds=300)
    root = "job_" + "8" * 32
    ex, requests, runs = _executor_with(
        lambda _: (root, "42", "accepted"), workspace_root=tmp_path)
    plan = heuristic_plan(req.task, complexity_signals={"work_type": "simple_tool"},
                          budget_caps=CAPS, allowed_modes=FIRST_VERSION_MODES)
    assert plan.mode == "single"
    record = ex.execute_plan(req, plan, budget_caps=CAPS, root_job_id=root)
    assert record["mode_requested"] == "single"
    assert record["draft_level"] == "accepted" and not record["subtasks"]
    assert len(runs) == 1 and runs[0]["job_id"] == root


def test_fixed_mode_remains_shared_fixed_policy(tmp_path):
    req = _req()
    root = "job_" + "9" * 32
    ex, requests, runs = _executor_with(
        lambda _: (root, "固定链结果", "accepted"), workspace_root=tmp_path)
    plan = heuristic_plan(req.task, complexity_signals={"work_type": "collection"},
                          budget_caps=CAPS, allowed_modes=("fixed",))
    record = ex.execute_plan(req, plan, budget_caps=CAPS, root_job_id=root)
    assert record["mode_requested"] == "fixed"
    assert record["draft_level"] == "accepted" and len(runs) == 1


def test_manager_worker_hands_dependency_outputs_downstream(tmp_path):
    req = _req(task="帮我调研 X 并写报告")
    root = "job_" + "a" * 32

    def script(current):
        if "检索并整理原始资料" in current.task:
            return ("job_r1", "研究员原始资料", "accepted")
        if "按目标整理素材" in current.task:
            return ("job_o1", "组织者素材包", "accepted")
        return (root, "最终报告", "accepted")

    ex, requests, runs = _executor_with(script, workspace_root=tmp_path)
    plan = heuristic_plan(req.task,
                          complexity_signals={"work_type": "research_report"},
                          budget_caps=CAPS, allowed_modes=FIRST_VERSION_MODES)
    assert plan.mode == "manager_worker"
    record = ex.execute_plan(req, plan, budget_caps=CAPS, root_job_id=root)
    assert [s["id"] for s in record["subtasks"]] == ["T1", "T2"]
    organizer_req = next(r for r in requests if "按目标整理素材" in r.task)
    assert any("研究员原始资料" in text for text in organizer_req.texts)
    root_req = requests[-1]
    joined = "\n".join(root_req.texts)
    assert "研究员原始资料" in joined and "组织者素材包" in joined
    assert record["draft_level"] == "accepted"


def test_mode_catalog_exposes_only_d6_open_modes():
    decision = default_capability_catalog().available(
        has_sources=True, network_available=True,
        available_tools=set(), model_available=True, max_cost_usd=1.0)
    assert decision.available == ["single", "fixed", "manager_worker", "fanout", "dynamic_team", "debate"]
    rejected = {item["mode"]: item["reason"] for item in decision.rejected}
    assert decision.rejected == []
    assert decision.rejected == []

def test_fanout_wave_runs_children_concurrently(tmp_path):
    import threading
    import time

    from tests.test_orchestration_s8 import _fanout_plan_dict

    root = "job_" + "b" * 32
    lock = threading.Lock()
    active = {"n": 0, "max": 0}

    def script(current):
        if "子题一" in current.task or "子题二" in current.task:
            with lock:
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
            time.sleep(0.12)
            with lock:
                active["n"] -= 1
            return ("job_s1" if "子题一" in current.task else "job_s2",
                    "并发子结果", "accepted")
        return (root, "并发汇总报告", "accepted")

    ex, _, _ = _executor_with(script, workspace_root=tmp_path)
    record = ex.execute_plan(
        _req(), from_plan_dict(_fanout_plan_dict()), budget_caps=CAPS,
        root_job_id=root)
    assert active["max"] >= 2
    assert record["draft_level"] == "accepted"
    assert record["execution_note"].startswith("fanout 按依赖波次并发")

def test_dynamic_team_replans_and_returns_structured_children(tmp_path):
    from src.llm.mock import MockLLM

    root = "job_" + "c" * 32
    requests = []

    def script(current):
        role = "organizer" if "整理" in current.task else (
            "writer" if "成稿" in current.task else "researcher")
        return (f"job_{role}", f"{role}动态产出", "accepted")

    def factory(workspace_root, settings, llm):
        def build(current):
            requests.append(current)
            class App:
                def run(self, job_id=None, parent_job_id=None):
                    from tests.test_orchestration_s8 import _Outcome
                    job, text, level = script(current)
                    return _Outcome(job, text, level)
            return App()
        return build

    ex = OrchestrationExecutor(workspace_root=tmp_path, llm=MockLLM(),
                               app_factory=factory)
    plan = heuristic_plan("帮我调研 X 并成稿",
                          complexity_signals={"dynamic_team": True},
                          budget_caps=CAPS, allowed_modes=FIRST_VERSION_MODES)
    assert plan.mode == "dynamic_team"
    record = ex.execute_plan(_req(task="帮我调研 X 并成稿"), plan,
                             budget_caps=CAPS, root_job_id=root)
    assert record["dynamic_team"]["worker_calls"] >= 1
    assert record["subtasks"] and all(item["result"] is not None
                                      for item in record["subtasks"])
    assert record["draft_level"] == "accepted"

def test_debate_mode_records_two_sides_and_review(tmp_path):
    from src.llm.mock import MockLLM
    from tests.test_orchestration_s8 import _Outcome

    root = "job_" + "f" * 32
    def script(current):
        if "pro side" in current.task:
            return ("job_pro", "pro evidence", "accepted")
        if "con side" in current.task:
            return ("job_con", "con evidence", "accepted")
        return (root, "debate report", "accepted")
    def factory(workspace_root, settings, llm):
        def build(current):
            class App:
                def run(self, job_id=None, parent_job_id=None):
                    return _Outcome(*script(current))
            return App()
        return build
    plan = from_plan_dict({
        "schema_version": "1", "mode": "debate", "reason": "test",
        "subtasks": [
            {"id": "PRO", "role": "researcher", "description": "pro side", "depends_on": []},
            {"id": "CON", "role": "editor", "description": "con side", "depends_on": []},
            {"id": "W", "role": "writer", "description": "write", "depends_on": ["PRO", "CON"]},
        ],
        "needs_reviewer": True, "max_parallel": 2,
        "budget": CAPS.as_dict(), "fallback_mode": "fixed", "expected": {},
    })
    ex = OrchestrationExecutor(workspace_root=tmp_path, llm=MockLLM(),
                               app_factory=factory)
    record = ex.execute_plan(_req(), plan, budget_caps=CAPS, root_job_id=root)
    assert [item["id"] for item in record["subtasks"]] == ["PRO", "CON"]
    assert record["debate"]["verdict"] and record["draft_level"] == "accepted"


def test_nested_subagent_depth_dedupe_count_and_permissions():
    from types import SimpleNamespace
    from src.harness.tools.subagent import (build_delegate_spec,
                                            subagent_permission_scope)

    seen_permissions = []
    holder = {}
    class RecursiveRuntime:
        def run_task(self, text, context=None, **kwargs):
            seen_permissions.append(tuple(sorted(context.permissions)))
            nested = holder["spec"].func("agent", text + " nested", 2)
            return SimpleNamespace(final_text=nested, status="completed",
                                   termination_reason="success")
    spec = build_delegate_spec(RecursiveRuntime(), max_depth=2, max_total=3)
    holder["spec"] = spec
    with subagent_permission_scope({"calculator"}):
        result = spec.func("agent", "root task", 3)
    assert "[depth-limit]" in result
    assert seen_permissions and all(p == ("calculator",) for p in seen_permissions)

    class StubRuntime:
        def run_task(self, text, context=None, **kwargs):
            return SimpleNamespace(final_text="ok", status="completed",
                                   termination_reason="success")
    stub = build_delegate_spec(StubRuntime(), max_depth=2, max_total=2)
    with subagent_permission_scope({"calculator"}):
        assert stub.func("agent", "same")
        duplicate = stub.func("agent", "same")
        assert "[duplicate]" in duplicate
        assert stub.func("agent", "other")
        limited = stub.func("agent", "third")
        assert "[count-limit]" in limited
