# -*- coding: utf-8 -*-
"""FastAPI 适配层（可选依赖，未安装自动跳过）与 React 前端页面静态检查。"""
import json
import time
from pathlib import Path

import pytest

REACT_INDEX = Path(__file__).resolve().parent.parent / "src" / "interfaces" / \
    "web" / "static" / "react" / "index.html"


def test_react_page_exists_and_uses_same_api():
    assert REACT_INDEX.exists()
    text = REACT_INDEX.read_text(encoding="utf-8")
    assert "/api/runs" in text                       # 消费同一 JSON 契约
    assert "workbench" in text                       # 离线回退提示指向标准库入口
    assert "innerHTML" not in text                   # 安全红线：不走 innerHTML


fastapi = pytest.importorskip("fastapi", reason="fastapi 未安装（可选依赖）")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from src.interfaces.web.fastapi_app import build_app
    return TestClient(build_app())


def test_health(client):
    data = client.get("/api/health").json()
    assert data["status"] == "ok" and data["adapter"] == "fastapi"


def test_index_serves_react_page(client):
    html = client.get("/").text
    assert "研究任务工作台" in html


def test_submit_and_poll_mock_run(client):
    resp = client.post("/api/runs", json={"goal": "计算 27*43 并说明结果",
                                          "mode": "mock", "max_calls": 3})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    # 后台线程执行：轮询直到终态（Mock 大脑，应当很快）
    deadline = time.time() + 30
    detail = {}
    while time.time() < deadline:
        detail = client.get(f"/api/runs/{job_id}").json()
        if detail["status"] != "running":
            break
        time.sleep(0.2)
    assert detail["status"] in ("completed", "partial", "cancelled", "failed")
    assert detail["job_id"] == job_id
    listing = client.get("/api/runs").json()
    assert any(j["job_id"] == job_id for j in listing["jobs"])


def test_unknown_job_404(client):
    assert client.get("/api/runs/job_" + "0" * 32).status_code == 404
