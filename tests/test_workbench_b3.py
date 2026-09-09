# -*- coding: utf-8 -*-
"""测试：Web 资料/产物只读端点（B3-05）。"""
import http.client
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

import pytest

from src.harness.storage.artifacts import ArtifactStore
from src.interfaces.web.workbench import make_server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def client():
    ws = os.path.join(ROOT, "workspaces", "_t_web3_" + uuid.uuid4().hex[:6])
    os.makedirs(ws, exist_ok=True)
    server = make_server(workspaces=ws, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    yield conn, ws
    conn.close()
    state = server.RequestHandlerClass.state
    with state.lock:
        workers = list(state.running.values())
    for worker in workers:
        worker.join(timeout=3)
    server.shutdown()
    server.server_close()
    shutil.rmtree(ws, ignore_errors=True)


def _get(conn, path):
    conn.request("GET", path)
    resp = conn.getresponse()
    raw = resp.read()
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        body = raw.decode("utf-8")
    return resp.status, body


def _post(conn, path, payload):
    conn.request("POST", path, body=json.dumps(payload),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    return resp.status, json.loads(raw) if raw else {}


def _wait_job_view(conn, run_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        status, data = _get(conn, f"/api/runs/{run_id}/job")
        if status == 200 and (data.get("ledger") or {}).get("root_job_id"):
            return data
        time.sleep(0.05)
    raise AssertionError("等待 job 账本超时")


def test_http_job_sources_list_and_full_text(client, tmp_path):
    conn, ws = client
    material = tmp_path / "资料.md"
    material.write_text("# 材料标题\n\n正文段落一。\n\n正文段落二。\n", encoding="utf-8")
    status, created = _post(conn, "/api/runs", {
        "task": "整理材料", "mode": "mock", "files": [str(material)],
        "texts": ["补充粘贴资料。"]})
    assert status == 200, created
    view = _wait_job_view(conn, created["run_id"])
    job_id = view["ledger"]["root_job_id"]
    status, listing = _get(conn, f"/api/jobs/{job_id}/sources")
    assert status == 200 and listing["usable"] == 2
    file_src = next(s for s in listing["sources"] if s["kind"] == "file")
    status, text = _get(conn, f"/api/jobs/{job_id}/sources/{file_src['source_id']}/text")
    assert status == 200
    assert text.startswith("# 材料标题") and "正文段落二" in text
    # 列表不含段落明细（定位在 meta 文件里，不在列表接口膨胀返回）
    assert "segments" not in listing["sources"][0]


def test_http_sources_duplicate_and_empty_have_no_full_text(client, tmp_path):
    conn, ws = client
    status, created = _post(conn, "/api/runs", {
        "task": "重复检测", "mode": "mock", "texts": ["相同内容。", "相同内容。", "  "],
        "max_calls": 0})
    assert status == 200
    view = _wait_job_view(conn, created["run_id"])
    job_id = view["ledger"]["root_job_id"]
    status, listing = _get(conn, f"/api/jobs/{job_id}/sources")
    assert listing["statuses"].get("duplicate") == 1
    assert listing["statuses"].get("empty") == 1
    dup = next(s for s in listing["sources"] if s["status"] == "duplicate")
    status, _ = _get(conn, f"/api/jobs/{job_id}/sources/{dup['source_id']}/text")
    assert status == 404


def test_http_artifacts_list_and_content(client):
    conn, ws = client
    job_id = "job_" + "a" * 32
    job_dir = os.path.join(ws, "jobs", job_id)
    store = ArtifactStore(Path(job_dir))
    store.save("report", "# 报告\n\n受控产物正文。\n", producer="test")
    status, listing = _get(conn, f"/api/jobs/{job_id}/artifacts")
    assert status == 200 and listing["artifacts"][0]["artifact_id"] == "report.v1"
    status, content = _get(conn, f"/api/jobs/{job_id}/artifacts/report.v1/content")
    assert status == 200 and content.startswith("# 报告")
    status, _ = _get(conn, f"/api/jobs/{job_id}/artifacts/report.v9/content")
    assert status == 404


def test_http_job_api_rejects_bad_ids_and_views(client):
    conn, ws = client
    for path in ("/api/jobs/../sources", "/api/jobs/abc/sources",
                 "/api/jobs/job_zzzz/sources", "/api/jobs/job_x/sources",
                 f"/api/jobs/{'b' * 32}/sources"):
        status, _ = _get(conn, path)
        assert status == 404
    job_id = "job_" + "c" * 32
    status, _ = _get(conn, f"/api/jobs/{job_id}/nope")
    assert status == 404
    status, data = _get(conn, f"/api/jobs/{job_id}/sources")
    assert status == 404  # 目录不存在视为无此任务
    os.makedirs(os.path.join(ws, "jobs", job_id), exist_ok=True)
    status, data = _get(conn, f"/api/jobs/{job_id}/sources")
    assert status == 200 and data["sources"] == []  # 存在但无来源的任务返回空列表


def test_index_page_mentions_library_panel(client):
    conn, ws = client
    conn.request("GET", "/")
    resp = conn.getresponse()
    html = resp.read().decode("utf-8")
    assert "资料与产物" in html and "loadLibrary" in html
    assert 'id="filepaths"' in html and 'id="pastetext"' in html and 'id="urls"' in html
    assert "split(/\\r?\\n/)" in html  # 正则未被 Python 字符串转义破坏
