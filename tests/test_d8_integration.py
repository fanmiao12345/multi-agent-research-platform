# -*- coding: utf-8 -*-
"""D7-03/04 与 D8-01～03：补做、来源更新、持久队列、取消和恢复审计。"""
import json

from src.application.orchestration import OrchestrationExecutor, from_plan_dict
from src.application.pipeline.model import MaterialPack
from src.application.request import TaskRequest
from src.harness.run_store import write_json
from src.harness.state.db import StateDb
from src.harness.state.queue import JobQueue
from src.harness.state.resume import inspect_resume_state
from src.harness.storage.artifacts import ArtifactStore
from src.harness.storage.sources import SourceStore
from tests._s4_pipeline_brain import S4Brain
from tests.test_orchestration_s8 import CAPS, _fanout_plan_dict, _req


def _store(tmp_path, texts):
    store = SourceStore(tmp_path)
    for index, text in enumerate(texts, start=1):
        store.add_paste(text, display_index=index)
    return store


def test_gap_repair_adds_source_once_and_continues(tmp_path, monkeypatch):
    from src.application.pipeline import runner as runner_module

    calls = {"material": 0, "repair": 0}

    def material(_llm, _goal, _evidence):
        calls["material"] += 1
        if calls["material"] == 1:
            return MaterialPack(topics=[], gaps=[{"question": "还缺什么？", "missing": "关键事实"}]), []
        return MaterialPack(topics=[{"name": "修复后", "points": []}], gaps=[]), []

    monkeypatch.setattr(runner_module, "run_material_stage", material)

    def repair(gaps):
        calls["repair"] += 1
        assert gaps and gaps[0]["question"] == "还缺什么？"
        return "补充事实一。\n\n补充事实二。"

    store = _store(tmp_path / "job", ("初始事实。",))
    result = runner_module.run_research_pipeline(
        llm=S4Brain(), job_dir=tmp_path / "job", store=store,
        goal="整理资料目录", delivery_kind="collection",
        repair_callback=repair)
    assert result.draft_level == "accepted"
    assert calls == {"material": 2, "repair": 1}
    assert store.summary()["total"] == 2


def test_source_withdrawal_revises_new_job_and_keeps_old(tmp_path):
    from src.application.research import revise_with_source_update

    root = tmp_path
    origin_id = "job_" + "1" * 32
    origin = root / "jobs" / origin_id
    store = SourceStore(origin)
    first = store.add_paste("source one fact A.\n\nsource one fact B.", display_index=1)
    store.add_paste("source two fact C.\n\nsource two fact D.", display_index=2)
    store.add_paste("source three fact E.\n\nsource three fact F.", display_index=3)
    ArtifactStore(origin).save("report", "# Old report\n\n[E-001] old.", producer="test")
    write_json(origin / "request.json", TaskRequest(
        "write report", flow="research", mode="mock").snapshot())
    outcome = revise_with_source_update(
        workspace_root=root, job_id=origin_id, source_id=first.source_id,
        reason="source retracted", instruction="revise without withdrawn source",
        llm=S4Brain())
    new_job = root / "jobs" / outcome.root_job_id
    update = json.loads((new_job / "source_update.json").read_text(encoding="utf-8"))
    assert update["withdrawn"]["source"]["status"] == "withdrawn"
    assert outcome.root_job_id != origin_id
    assert (origin / "artifacts" / "report.v1.md").exists()
    new_sources = SourceStore(new_job).summary()["sources"]
    assert all("source one" not in (SourceStore(new_job).full_text(s["source_id"]) or "")
               for s in new_sources)


def test_persistent_queue_records_root_plan_and_child(tmp_path):
    db = StateDb(tmp_path / "state.sqlite")
    queue = JobQueue(db)
    root = "job_" + "2" * 32

    def factory(workspace_root, settings, llm):
        def build(current):
            class App:
                def run(self, job_id=None, parent_job_id=None):
                    from tests.test_orchestration_s8 import _Outcome
                    return _Outcome("job_child", "child", "accepted") if "子题一" in current.task \
                        else _Outcome("job_child2", "child2", "accepted") if "子题二" in current.task \
                        else _Outcome(root, "final", "accepted")
            return App()
        return build

    ex = OrchestrationExecutor(workspace_root=tmp_path, app_factory=factory,
                               state_queue=queue)
    plan = from_plan_dict(_fanout_plan_dict() | {"max_parallel": 1})
    record = ex.execute_plan(_req(), plan,
                             budget_caps=CAPS, root_job_id=root)
    root_row = queue.get(root)
    child_row = queue.get("job_child")
    assert root_row["plan_version"] == 1
    assert child_row["parent_job_id"] == root
    assert record["draft_level"] == "accepted"


def test_cancel_stops_later_branches(tmp_path):
    root = "job_" + "3" * 32
    calls = []

    def factory(workspace_root, settings, llm):
        def build(current):
            class App:
                def run(self, job_id=None, parent_job_id=None):
                    from tests.test_orchestration_s8 import _Outcome
                    calls.append(current.task)
                    return _Outcome("job_" + str(len(calls)), "partial", "draft")
            return App()
        return build

    ex = OrchestrationExecutor(workspace_root=tmp_path, app_factory=factory)
    plan = from_plan_dict(_fanout_plan_dict() | {"max_parallel": 1})
    record = ex.execute_plan(_req(), plan,
                             budget_caps=CAPS, root_job_id=root,
                             should_stop=lambda: len(calls) >= 1)
    assert record["termination_reason"] == "cancelled"
    assert len(calls) == 1


def test_resume_inspection_keeps_unknowns_and_budget_history(tmp_path):
    job = tmp_path / "jobs" / ("job_" + "4" * 32)
    job.mkdir(parents=True)
    store = SourceStore(job)
    source = store.add_paste("source fact", display_index=1)
    (job / "stage_material.json").write_text("{}", encoding="utf-8")
    write_json(job / "orchestration.json", {"subtasks": [{
        "child_job_id": "job_child", "draft_level": "accepted",
        "result": {"summary": "ok", "evidence_refs": []}}]})
    write_json(job / "ledger.json", {"calls": [{"call_id": "c1", "estimated_cost_usd": None}],
                                     "reservations": [], "unknown_usage_calls": 1})
    db = StateDb(tmp_path / "state.sqlite")
    with db.write_tx() as conn:
        conn.execute(
            "INSERT INTO operations(op_key, job_id, action, action_version, params_hash,"
            " status, result_json, error_type, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("op", job.name, "external", 1, "h", "unknown", "", "", 0, 0))
    state = inspect_resume_state(job, state_db=db)
    assert state["committed_stages"] == ["stage_material"]
    assert state["reusable_children"][0]["child_job_id"] == "job_child"
    assert state["budget_history"]["unknown_usage_calls"] == 1
    assert state["unknown_operations"][0]["action"] == "external"
    assert source.source_id in {s["source_id"] for s in state["reusable_sources"]}