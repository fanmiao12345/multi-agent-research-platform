# -*- coding: utf-8 -*-
"""D8-04 + D9：待输入/审批持久化与统一工作台功能。"""
import http.client
import json
import shutil
import threading
import uuid
from pathlib import Path

import pytest

from src.harness.run_store import write_json
from src.harness.state.approvals import ApprovalError, ApprovalStore
from src.harness.state.db import StateDb
from src.harness.state.pending_inputs import PendingInputStore
from src.harness.storage.artifacts import ArtifactStore
from src.interfaces.web.workbench import make_server

ROOT = Path(__file__).resolve().parent.parent


def _request(conn, method, path, payload=None):
    body = json.dumps(payload) if payload is not None else ""
    conn.request(method, path, body=body,
                 headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    raw = response.read().decode("utf-8")
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = raw
    return response.status, data, dict(response.getheaders())


@pytest.fixture()
def client():
    ws = ROOT / "workspaces" / ("_t_d9_" + uuid.uuid4().hex[:6])
    ws.mkdir(parents=True, exist_ok=True)
    from tests._s4_pipeline_brain import S4Brain
    import src.harness.models.factory as factory
    old = factory.build_adapter
    factory.build_adapter = lambda *a, **kw: S4Brain()
    server = make_server(workspaces=ws, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=20)
    yield conn, ws, server
    conn.close()
    state = server.RequestHandlerClass.state
    state.stop_worker()
    server.shutdown()
    server.server_close()
    factory.build_adapter = old
    shutil.rmtree(ws, ignore_errors=True)


def test_pending_input_expiry_answer_and_approval_invalidation(tmp_path):
    db = StateDb(tmp_path / "state.sqlite")
    now = [100.0]
    pending = PendingInputStore(db, clock=lambda: now[0])
    item = pending.create(job_id="job_x", questions=["which object?"],
                          target="compare", params={"task": "compare"},
                          budget={"max_calls": 2}, plan_version=1,
                          ttl_seconds=10)
    assert pending.get(item["input_id"], now=105)["status"] == "pending"
    answered = pending.answer(item["input_id"], {"object": "A and B"})
    assert answered["status"] == "answered"

    second = pending.create(job_id="job_y", questions=["missing"],
                            target="compare", params={}, budget={}, ttl_seconds=5)
    assert pending.get(second["input_id"], now=106)["status"] == "expired"
    assert pending.invalidate("job_y") == 0

    approvals = ApprovalStore(db, "job_y", clock=lambda: now[0])
    approval = approvals.create("external", {"path": "x"}, ttl_seconds=10)
    approvals.decide(approval["approval_id"], "approve")
    assert approvals.valid_for("external", {"path": "x"}) is not None
    expiring = approvals.create("expire", {"path": "x"}, ttl_seconds=10)
    now[0] = 111
    with pytest.raises(ApprovalError, match="过期"):
        approvals.decide(expiring["approval_id"], "approve")
    changed = approvals.create("external", {"path": "changed"}, ttl_seconds=10)
    assert approvals.invalidate_for("external") == 1
    assert next(a for a in approvals.list() if a["approval_id"] == changed["approval_id"])["status"] == "invalid"


def test_web_plan_only_and_waiting_input_flow(client):
    conn, _, server = client
    status, planned, _ = _request(conn, "POST", "/api/runs", {
        "task": "写一份报告", "flow": "research", "texts": ["source fact"],
        "plan_only": True})
    assert status == 200 and planned["status"] == "planned"
    assert planned["plan"]["mode"] in ("single", "fixed", "manager_worker", "fanout")

    status, waiting, _ = _request(conn, "POST", "/api/runs", {
        "task": "比较不同方案", "flow": "research", "texts": ["source fact"]})
    assert status == 200 and waiting["status"] == "waiting_input"
    job_id = waiting["job_id"]
    status, progress, _ = _request(conn, "GET", f"/api/jobs/{job_id}/progress")
    assert status == 200 and progress["pending_inputs"][0]["status"] == "pending"
    input_id = progress["pending_inputs"][0]["input_id"]
    status, answered, _ = _request(
        conn, "POST", f"/api/jobs/{job_id}/input",
        {"answer": {"object": "方案 A 与方案 B"}})
    assert status == 200 and answered["status"] == "queued"
    assert server.RequestHandlerClass.state.pending_inputs.get(input_id)["status"] == "answered"


def test_web_export_html_and_process_record(client):
    conn, ws, _ = client
    job_id = "job_" + "9" * 32
    job = ws / "jobs" / job_id
    ArtifactStore(job).save("report", "# Report\n\n[E-001] supported.", producer="test")
    write_json(job / "orchestration.json", {"root_job_id": job_id, "status": "finished"})
    status, page, headers = _request(conn, "GET", f"/api/jobs/{job_id}/export.html")
    assert status == 200 and "<pre>" in page and "attachment" in headers["Content-Disposition"]
    status, process, _ = _request(conn, "GET", f"/api/jobs/{job_id}/process")
    assert status == 200 and process["status"] == "finished"