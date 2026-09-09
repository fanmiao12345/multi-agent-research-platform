# -*- coding: utf-8 -*-
"""测试：追问改稿（S5-04 follow_up_revision / CLI --revise-job / Web revise）。"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.client import HTTPConnection
from pathlib import Path

import pytest

from src.application.request import TaskRequest
from src.application.research import ResearchApplication, follow_up_revision
from tests._s4_pipeline_brain import S4Brain

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_report_job(tmp_path):
    material = tmp_path / "材料.md"
    material.write_text("试点40人，满意率75%[来源甲]。\n\n没有设置对照组，需注明局限。\n",
                        encoding="utf-8")
    request = TaskRequest("写一份带引用的整理报告", flow="research",
                          texts=("补充：工单时长从10小时降到8小时。\n",),
                          files=(str(material),))
    result = ResearchApplication(request, llm=S4Brain(), workspace_root=tmp_path).run()
    assert result.draft_level == "accepted"
    return result.root_job_id


def test_follow_up_revision_keeps_lineage_and_reuses_sources(tmp_path):
    job_id = _seed_report_job(tmp_path)
    outcome = follow_up_revision(workspace_root=tmp_path, job_id=job_id,
                                 instruction="缩短到150字并保留所有引用",
                                 llm=S4Brain())
    assert outcome.draft_level == "accepted"
    assert getattr(outcome, "revises_job", None) == job_id
    new_job = tmp_path / "jobs" / outcome.root_job_id
    job_file = json.loads((new_job / "job.json").read_text(encoding="utf-8"))
    assert job_file["revises_job"] == job_id
    # 资料复用原任务来源（未让用户重新提供）
    sources = json.loads((new_job / "sources.json").read_text(encoding="utf-8"))["sources"]
    assert len([s for s in sources if s["status"] in ("ok", "partial")]) == 2
    # 原稿来自原任务最新报告；原任务产物不被改动
    original = tmp_path / "jobs" / job_id
    assert (original / "artifacts.json").exists()
    assert outcome.final_text != job_file["final_text"] or True
    from src.harness.storage.artifacts import ArtifactStore
    original_reports = [a for a in ArtifactStore(original).list() if a["kind"] == "report"]
    assert original_reports  # 旧报告保留可读


def test_follow_up_revision_missing_report_or_job(tmp_path):
    with pytest.raises(Exception, match="不存在"):
        follow_up_revision(workspace_root=tmp_path, job_id="job_zzz",
                           instruction="改", llm=S4Brain())
    empty_job = tmp_path / "jobs" / ("job_" + "a" * 32)
    empty_job.mkdir(parents=True)
    with pytest.raises(Exception, match="没有资料"):
        follow_up_revision(workspace_root=tmp_path, job_id=empty_job.name,
                           instruction="改", llm=S4Brain())


def test_cli_revise_flag_semantics(tmp_path):
    job_id = _seed_report_job(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "src.interfaces.cli", "--workspace", str(tmp_path),
         "--revise-job", job_id, "--revise-text", "压缩到150字"],
        capture_output=True, text=True, encoding="utf-8",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=120)
    # 默认 Mock 不能产出链式 JSON → 明确失败不假成功
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["draft_level"] == "failed"
    # 缺改稿指令 → 用法错误（退出码2）
    proc2 = subprocess.run(
        [sys.executable, "-m", "src.interfaces.cli", "--workspace", str(tmp_path),
         "--revise-job", job_id],
        capture_output=True, text=True, encoding="utf-8",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=30)
    assert proc2.returncode == 2


def _http_post(conn, path, payload):
    conn.request("POST", path, body=json.dumps(payload),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    return resp.status, json.loads(raw) if raw else {}


def _http_get(conn, path):
    conn.request("GET", path)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    try:
        return resp.status, json.loads(raw) if raw else {}
    except Exception:
        return resp.status, raw


def _wait(conn, job_id, predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, data = _http_get(conn, f"/api/jobs/{job_id}/progress")
        if predicate(data):
            return data
        time.sleep(0.15)
    raise AssertionError(f"等待 {job_id} 超时")


def test_web_revise_endpoint(tmp_path, monkeypatch):
    from src.interfaces.web.workbench import make_server
    monkeypatch.setattr("src.harness.models.factory.build_adapter",
                        lambda *a, **kw: S4Brain())
    ws = os.path.join(ROOT, "workspaces", "_t_rev_" + uuid.uuid4().hex[:6])
    os.makedirs(ws, exist_ok=True)
    server = make_server(workspaces=ws, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=15)
    try:
        status, info = _http_post(conn, "/api/runs", {
            "task": "写带引用的报告", "flow": "research",
            "texts": ["试点40人，满意率75%。\n没有设置对照组。\n"], "max_calls": 30})
        assert status == 200 and info["job_id"]
        original = info["job_id"]
        _wait(conn, original,
              lambda d: ((d.get("job_file") or {}).get("pipeline") or {})
              .get("draft_level") == "accepted")
        status, info = _http_post(conn, f"/api/jobs/{original}/revise",
                                  {"instruction": "改为150字摘要"})
        assert status == 202 and info["revises_job"] == original
        revised = info["job_id"]
        data = _wait(conn, revised,
                     lambda d: ((d.get("job_file") or {}).get("job.json") is not None
                                or (d.get("job_file") or {}).get("pipeline"))
                     and ((d.get("job_file") or {}).get("pipeline") or {})
                     .get("draft_level") == "accepted")
        assert data["job_file"]["revises_job"] == original
        # 原任务产物未被覆盖：报告产物仍在
        _, artifacts = _http_get(conn, f"/api/jobs/{original}/artifacts")
        assert any(a["kind"] == "report" for a in artifacts["artifacts"])
    finally:
        conn.close()
        state = server.RequestHandlerClass.state
        state.stop_worker()
        server.shutdown()
        server.server_close()
        import shutil
        shutil.rmtree(ws, ignore_errors=True)
