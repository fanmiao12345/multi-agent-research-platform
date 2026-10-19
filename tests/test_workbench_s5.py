# -*- coding: utf-8 -*-
"""测试：S5 Web 工作台研究任务（队列/进度/取消/恢复/证据/导出/安全/重启持久）。"""
import http.client
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

import pytest

from src.interfaces.web.workbench import make_server
from tests._s4_pipeline_brain import S4Brain

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def client(monkeypatch):
    ws = os.path.join(ROOT, "workspaces", "_t_web5_" + uuid.uuid4().hex[:6])
    os.makedirs(ws, exist_ok=True)
    monkeypatch.setattr("src.harness.models.factory.build_adapter",
                        lambda *a, **kw: S4Brain())
    server = make_server(workspaces=ws, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    yield conn, ws, server, monkeypatch
    conn.close()
    state = server.RequestHandlerClass.state
    state.stop_worker()
    with state.lock:
        workers = list(state.running.values())
    for worker in workers:
        worker.join(timeout=2)
    server.shutdown()
    server.server_close()
    shutil.rmtree(ws, ignore_errors=True)


def _post(conn, path, payload=None, headers=None):
    body = json.dumps(payload) if payload is not None else ""
    conn.request("POST", path, body=body,
                 headers={"Content-Type": "application/json",
                          **(headers or {})})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = raw
    return resp.status, data, dict(resp.getheaders())


def _get(conn, path, headers=None):
    conn.request("GET", path, headers=headers or {})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = raw
    return resp.status, data, dict(resp.getheaders())


def _wait_job(conn, job_id, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        status, data, _ = _get(conn, f"/api/jobs/{job_id}/progress")
        if status == 200 and predicate(data):
            return data
        last = data
        time.sleep(0.1)
    _, listing, _ = _get(conn, "/api/jobs")
    raise AssertionError(
        f"等待 job {job_id} 超时。last={json.dumps(last, ensure_ascii=False)[:400]} "
        f"jobs={json.dumps(listing, ensure_ascii=False)[:400]}")


def _submit_research(conn, **extra):
    payload = {"task": "写一份带引用的整理报告", "flow": "research",
               "texts": ["甲资料：结论甲为 42%。\n乙资料：方法乙可复现。\n"],
               "max_calls": 20}
    payload.update(extra)
    status, info, _ = _post(conn, "/api/runs", payload)
    assert status == 200, info
    assert info["status"] == "queued" and info["job_id"].startswith("job_")
    return info["job_id"]


def test_research_job_full_lifecycle_and_views(client):
    conn, ws, server, _ = client
    job_id = _submit_research(conn)
    progress = _wait_job(conn, job_id,
                         lambda d: (d.get("job_file") or {}).get("pipeline"))
    flat = progress["job_file"]["pipeline"]
    assert flat["draft_level"] == "accepted" and flat["termination_reason"] == "success"
    # 报告产物与证据端点
    status, artifacts, _ = _get(conn, f"/api/jobs/{job_id}/artifacts")
    reports = [a for a in artifacts["artifacts"] if a["kind"] == "report"]
    assert reports and reports[-1]["artifact_id"].startswith("report.v")
    latest = reports[-1]["artifact_id"]
    status, content, _ = _get(conn, f"/api/jobs/{job_id}/artifacts/{latest}/content")
    assert status == 200 and "# S4报告" in content and "[E-00" in content
    status, evidence, _ = _get(conn, f"/api/jobs/{job_id}/evidence")
    assert status == 200 and len(evidence["evidence"]) >= 1
    item = evidence["evidence"][0]
    assert item["evidence_id"].startswith("E-") and item.get("start", -1) >= 0
    # 下载是附件（不执行），文件名来自登记产物
    status, body, headers = _get(conn, f"/api/jobs/{job_id}/artifacts/{latest}/download")
    assert status == 200 and "attachment" in headers.get("Content-Disposition", "")
    assert body == content
    # 队列行终态与 job 列表
    row = (server.RequestHandlerClass.state).queue.get(job_id)
    assert row["status"] == "completed"
    status, listing, _ = _get(conn, "/api/jobs")
    assert any(j["job_id"] == job_id for j in listing["jobs"])


def test_research_job_cancel_converges(client, monkeypatch):
    conn, ws, server, _ = client
    calls = []

    class SlowBrain(S4Brain):
        def _reply(self, purpose, user):
            if purpose == "material":
                calls.append("material")
                time.sleep(2.5)
            return super()._reply(purpose, user)
    monkeypatch.setattr("src.harness.models.factory.build_adapter",
                        lambda *a, **kw: SlowBrain())
    job_id = _submit_research(conn)
    # 等证据阶段完成（stage 钩子已记录）且材料阶段仍在执行时请求取消
    _wait_job(conn, job_id,
              lambda d: (d.get("job") or {}).get("stage") == "evidence:completed")
    status, outcome, _ = _post(conn, f"/api/jobs/{job_id}/cancel", {})
    assert status == 200 and outcome["status"] == "requested"
    progress = _wait_job(conn, job_id,
                         lambda d: (d.get("job") or {}).get("status") == "cancelled",
                         timeout=15)
    job_file = progress.get("job_file") or {}
    assert job_file.get("status") == "cancelled"
    assert "取消" in (job_file.get("pipeline") or {}).get("message", "")
    # 已保存的阶段产物保留（证据/素材），不会假装完成
    job_dir = Path(ws) / "jobs" / job_id
    assert (job_dir / "evidence.json").exists()


def test_research_job_resume_after_failure(client, monkeypatch):
    conn, ws, server, _ = client

    class CrashBrain(S4Brain):
        def _reply(self, purpose, user):
            if purpose == "outline":
                raise RuntimeError("模拟进程中断")
            return super()._reply(purpose, user)
    monkeypatch.setattr("src.harness.models.factory.build_adapter",
                        lambda *a, **kw: CrashBrain())
    job_id = _submit_research(conn)
    _wait_job(conn, job_id, lambda d: (d.get("job_file") or {}).get("error") is not None)
    # 切回好大脑并请求恢复
    monkeypatch.setattr("src.harness.models.factory.build_adapter",
                        lambda *a, **kw: S4Brain())
    status, info, _ = _post(conn, f"/api/jobs/{job_id}/resume", {})
    assert status == 202 and info["status"] == "resuming"
    progress = _wait_job(
        conn, job_id,
        lambda d: ((d.get("job_file") or {}).get("pipeline") or {})
        .get("draft_level") == "accepted", timeout=30)
    assert progress["job_file"]["status"] == "completed"


def test_write_api_security_guards(client):
    conn, ws, server, _ = client
    payload = json.dumps({"task": "t"})
    status, _, _ = _post(conn, "/api/runs", json.loads(payload),
                         headers={"Host": "evil.example"})
    assert status == 403
    status, _, _ = _post(conn, "/api/runs", json.loads(payload),
                         headers={"Host": "127.0.0.1"})
    assert status == 200
    conn.request("POST", "/api/runs", body="x" * (1024 * 1024 + 1),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    assert resp.status == 413
    resp.read()
    conn.request("POST", "/api/runs", body=json.dumps({"task": "t"}),
                 headers={"Content-Type": "text/plain"})
    resp = conn.getresponse()
    assert resp.status == 415
    resp.read()
    # 读接口不受 Host 限制影响
    status, _, _ = _get(conn, "/api/runs", headers={"Host": "evil.example"})
    assert status == 200


def test_jobs_survive_server_restart(client):
    conn, ws, server, _ = client
    job_id = _submit_research(conn)
    _wait_job(conn, job_id, lambda d: (d.get("job_file") or {}).get("status") == "completed")
    # 模拟重启：同工作区/同状态库起第二个服务实例
    second = make_server(workspaces=ws, port=0)
    thread = threading.Thread(target=second.serve_forever, daemon=True)
    thread.start()
    try:
        port = second.server_address[1]
        conn2 = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        status, data, _ = _get(conn2, "/api/jobs")
        assert any(j["job_id"] == job_id and j["status"] == "completed"
                   for j in data["jobs"])
        status, progress, _ = _get(conn2, f"/api/jobs/{job_id}/progress")
        assert (progress.get("job_file") or {}).get("status") == "completed"
        conn2.close()
    finally:
        second.shutdown()
        second.server_close()


def test_index_page_has_s5_panels(client):
    conn, ws, server, _ = client
    conn.request("GET", "/")
    resp = conn.getresponse()
    html = resp.read().decode("utf-8")
    assert 'id="flow"' in html and 'id="jobtbl"' in html
    assert 'id="reportview"' in html and 'id="btnexport"' in html
    assert "loadJobs" in html and "renderReportMarkdown" in html
    # 任务硬约束字段（S6-05 对齐）在表单上可用，且随 startRun 提交
    assert 'id="requireSections"' in html and 'id="forbidClaims"' in html
    assert 'id="keyFacts"' in html and "required_sections:lines(" in html


def test_research_job_hard_requirements_round_trip(client):
    """Web 表单的必需章节/禁语/关键事实进入请求、链内复验并可在进度里看到读数。"""
    conn, ws, server, _ = client
    job_id = _submit_research(conn, required_sections=["资料目录", "覆盖范围"],
                              forbidden_claims=["全体满意"],
                              key_facts=["不存在的事实"])
    progress = _wait_job(conn, job_id,
                         lambda d: (d.get("job_file") or {}).get("pipeline"))
    flat = progress["job_file"]["pipeline"]
    assert flat["draft_level"] == "accepted"
    # S4Brain 按提纲写章节：缺的必需章节由程序补入提纲，故 2/2 命中；硬要求可查
    assert flat["hard_checks"]["required_sections_total"] == 2
    assert flat["hard_checks"]["required_section_hits"] == 2
    assert flat["hard_checks"]["forbidden_hits"] == 0
    job_dir = Path(ws) / "jobs" / job_id
    saved = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
    assert saved["required_sections"] == ["资料目录", "覆盖范围"]
    assert saved["forbidden_claims"] == ["全体满意"] and saved["key_facts"] == ["不存在的事实"]
    pipeline = json.loads((job_dir / "pipeline.json").read_text(encoding="utf-8"))
    assert pipeline["hard_requirements"]["required_sections"] == ["资料目录", "覆盖范围"]
    # 进度端点也把硬约束读数暴露给页面
    assert (progress.get("pipeline") or {}).get("result", {}).get("hard_checks")


def _stop_worker(state):
    state.stop_worker()
    with state.lock:
        worker = state._worker_thread
    if worker is not None:
        worker.join(timeout=3)


def _interrupt_research_job(conn, server, *, unknown_operation=False):
    """跑完一次研究任务后模拟“进程中断”：删除终态文件、队列置 interrupted。"""
    job_id = _submit_research(conn)
    _wait_job(conn, job_id,
              lambda d: (d.get("job_file") or {}).get("status") == "completed")
    state = server.RequestHandlerClass.state
    _stop_worker(state)
    job_dir = Path(state.workspaces) / "jobs" / job_id
    (job_dir / "job.json").unlink()
    if unknown_operation:
        from src.harness.state.ops import OperationLedger
        OperationLedger(state.state_db, job_id).record(
            "external", "unknown", {"action": "test"})
    with state.state_db.write_tx() as db_conn:
        db_conn.execute(
            "UPDATE jobs SET status='interrupted', lease_owner='',"
            " lease_expires=0 WHERE job_id=?", (job_id,))
    return job_id, job_dir


def test_start_background_scans_then_starts_worker(tmp_path):
    from src.interfaces.web.workbench import WorkbenchState
    state = WorkbenchState(tmp_path)
    calls = []
    state.queue.startup_scan = lambda: calls.append("scan") or []
    state._ensure_worker = lambda: calls.append("worker")
    try:
        assert state.start_background() == []
        assert calls == ["scan", "worker"]
    finally:
        state.state_db.close()


def test_resume_decision_keeps_orchestration_jobs_manual(tmp_path):
    from src.interfaces.web.workbench import WorkbenchState
    state = WorkbenchState(tmp_path)
    job_dir = tmp_path / "jobs" / ("job_" + "a" * 32)
    job_dir.mkdir(parents=True)
    (job_dir / "orchestration.json").write_text("{}", encoding="utf-8")
    try:
        action, reason = state._resume_decision(job_dir)
        assert action == "blocked" and "人工" in reason
    finally:
        state.state_db.close()


def test_queued_job_runs_when_server_starts(tmp_path, monkeypatch):
    from config.settings import Settings
    from src.application.request import TaskRequest
    from src.interfaces.web.workbench import WorkbenchState
    monkeypatch.setattr("src.harness.models.factory.build_adapter",
                        lambda *a, **kw: S4Brain())
    settings = Settings(workspace_dir=tmp_path)
    seed = WorkbenchState(tmp_path, settings=settings)
    request = TaskRequest(task="写一份带引用的整理报告", mode="mock",
                          texts=("甲资料：结论甲为 42%。\n乙资料：方法乙可复现。\n",),
                          flow="research", max_calls=20)
    payload = request.snapshot()
    payload["texts"] = list(request.texts)
    job_id = seed.queue.submit(kind="research", request=payload, stage="queued")
    seed.state_db.close()
    server = make_server(workspaces=tmp_path, port=0, settings=settings)
    try:
        row = {}
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            row = server.RequestHandlerClass.state.queue.get(job_id) or {}
            if row.get("status") == "completed":
                break
            time.sleep(0.1)
        assert row.get("status") == "completed", row
        job_file = json.loads(
            (tmp_path / "jobs" / job_id / "job.json").read_text(encoding="utf-8"))
        assert (job_file.get("pipeline") or {}).get("draft_level") == "accepted"
    finally:
        server.RequestHandlerClass.state.stop_worker()
        server.server_close()


def test_interrupted_research_job_auto_resumes_on_restart(client):
    conn, ws, server, _ = client
    job_id, job_dir = _interrupt_research_job(conn, server)
    second = make_server(workspaces=ws, port=0)
    try:
        row = {}
        job_file = {}
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            row = second.RequestHandlerClass.state.queue.get(job_id) or {}
            try:
                job_file = json.loads(
                    (job_dir / "job.json").read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                job_file = {}
            if row.get("status") == "completed" and job_file.get("resume") is True:
                break
            time.sleep(0.1)
        assert row.get("status") == "completed", row
        assert job_file.get("resume") is True
        pipeline = job_file.get("pipeline") or {}
        assert pipeline.get("draft_level") == "accepted"
        assert any(stage.get("status") == "resumed"
                   for stage in pipeline.get("stages") or [])
    finally:
        _stop_worker(second.RequestHandlerClass.state)
        second.server_close()


def test_interrupted_research_job_with_unknown_operation_stays_manual(client):
    conn, ws, server, _ = client
    job_id, job_dir = _interrupt_research_job(conn, server, unknown_operation=True)
    second = make_server(workspaces=ws, port=0)
    try:
        row = {}
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            row = second.RequestHandlerClass.state.queue.get(job_id) or {}
            if row.get("status") == "failed":
                break
            time.sleep(0.1)
        assert row.get("status") == "failed", row
        assert row.get("error") == "resume_blocked"
        assert "人工" in (row.get("message") or "")
        assert not (job_dir / "job.json").exists()
    finally:
        _stop_worker(second.RequestHandlerClass.state)
        second.server_close()


def test_manual_resume_rejected_while_worker_owns_job(client):
    conn, ws, server, _ = client
    job_id = _submit_research(conn)
    _wait_job(conn, job_id,
              lambda d: (d.get("job_file") or {}).get("status") == "completed")
    state = server.RequestHandlerClass.state
    job_dir = Path(ws) / "jobs" / job_id
    (job_dir / "job.json").unlink()
    with state.state_db.write_tx() as db_conn:
        db_conn.execute(
            "UPDATE jobs SET status='running', lease_owner='other',"
            " lease_expires=? WHERE job_id=?",
            (time.time() + 600, job_id))
    status, info, _ = _post(conn, f"/api/jobs/{job_id}/resume", {})
    assert status == 409 and "运行" in info["error"]
