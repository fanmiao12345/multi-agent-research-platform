# -*- coding: utf-8 -*-
"""测试：Web Agent Workbench API（M11 步骤 111-119，端到端 HTTP）。"""
import http.client
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

import pytest

from src.harness.durable import save_plan
from src.harness.planning.task import Plan, Task
from src.interfaces.web.workbench import make_server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def client():
    ws = os.path.join(ROOT, "workspaces", "_t_web_" + uuid.uuid4().hex[:6])
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
        pending = [(rid, r["request_id"]) for rid, r in state.pending.items()]
        workers = list(state.running.values())
    for rid, request_id in pending:
        try:
            state.decide(rid, request_id, "reject")
        except LookupError:
            pass
    for worker in workers:
        worker.join(timeout=3)
    server.shutdown()
    server.server_close()
    shutil.rmtree(ws, ignore_errors=True)


def _get(conn, path):
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    return resp.status, json.loads(body) if body else {}


def _post(conn, path, payload):
    conn.request("POST", path, body=json.dumps(payload),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    return resp.status, json.loads(body) if body else {}


def test_index_and_run_dashboard(client):
    conn, ws = client
    conn.request("GET", "/")
    resp = conn.getresponse()
    assert resp.status == 200
    html = resp.read().decode("utf-8")
    assert html.startswith("<!doctype html>")
    assert '<input id="task"' in html
    assert "Agent Workbench" in html
    status, data = _get(conn, "/api/runs")
    assert status == 200 and data["runs"] == []


def test_config_api_and_real_missing_key_are_safe(client, monkeypatch):
    conn, ws = client
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("MODEL_API_KEY", "")
    status, report = _get(conn, "/api/config?mode=real")
    assert status == 200 and not report["ready"]
    assert "MODEL_API_KEY" in report["errors"][0]
    assert not report["connection_tested"]
    status, result = _post(conn, "/api/runs", {"task": "你好", "mode": "real"})
    assert status == 400 and "MODEL_API_KEY" in result["error"]
    assert _get(conn, "/api/runs")[1]["runs"] == []
    assert _get(conn, "/api/config?mode=mock")[1]["ready"]
    assert not _get(conn, "/api/config?mode=")[1]["ready"]


def test_http_root_budget_and_ledger_view(client):
    conn, ws = client
    status, created = _post(conn, "/api/runs", {"task": "计算6*7", "mode": "mock", "max_calls": 0})
    assert status == 200
    # run.json 的 root_job_id 在 run 启动瞬间才写入，期间 /job 返回 {"note":…}，
    # 谓词必须容忍该窗口（偶发 AttributeError 修复，B3-00）。
    view = _wait_view(conn, created["run_id"], "job",
                      lambda d: (d.get("ledger") or {}).get("status") == "cancelled")
    assert view["ledger"]["call_count"] == 0 and view["ledger"]["stop_reason"] == "call_limit"
    assert "root_job_id" in _get(conn, f"/api/runs/{created['run_id']}/meta")[1]


def test_config_api_omits_key_and_endpoint(client, monkeypatch):
    conn, ws = client
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("MODEL_API_KEY", "YOUR_API_KEY_HERE")
    monkeypatch.setenv("MODEL_BASE_URL", "https://api.example/v1")
    status, report = _get(conn, "/api/config?mode=real")
    assert status == 200 and report["ready"]
    assert "YOUR_API_KEY_HERE" not in json.dumps(report)
    assert "api.example" not in json.dumps(report)


def test_real_eval_does_not_display_mock_report(client, monkeypatch):
    conn, ws = client
    root = Path(ws)
    mock_report = root / "mock-report.json"
    mock_report.write_text(json.dumps({"meta": {"mode": "mock"}, "totals": {"passed": 5}}))
    monkeypatch.setattr("src.interfaces.web.workbench.EVAL_REPORT", mock_report)
    monkeypatch.setattr("src.interfaces.web.workbench.REAL_EVAL_REPORT", root / "missing.json")
    assert _get(conn, "/api/eval?mode=mock")[1]["totals"]["passed"] == 5
    real = _get(conn, "/api/eval?mode=real")[1]
    assert real["mode"] == "real" and "totals" not in real
    assert _get(conn, "/api/eval?mode=invalid")[0] == 400


def test_start_run_streaming_and_trace(client):
    conn, ws = client
    status, created = _post(conn, "/api/runs",
                            {"task": "帮我计算 6*7", "force_mock": True,
                             "max_iterations": 6})
    assert status == 200 and created["status"] == "ok"
    run_id = created["run_id"]
    assert run_id

    # Streaming 轮询：直到 run_end（113）
    after = 0
    seen = set()
    for _ in range(200):
        status, page = _get(conn, f"/api/runs/{run_id}/events?after={after}")
        after = page["after"]
        seen.update(e["type"] for e in page["new"])
        if "run_end" in seen:
            break
        time.sleep(0.05)
    assert "run_end" in seen and "llm_call" in seen

    # Trace / Timeline（117/112）
    status, trace = _get(conn, f"/api/runs/{run_id}/trace")
    assert status == 200 and len(trace["events"]) >= 4
    fields = {"run_id", "node", "type", "timestamp"}
    assert fields <= set(trace["events"][0])

    # Tool Cards（114）：calculator 调用存在且带 result/latency
    status, tools = _get(conn, f"/api/runs/{run_id}/tools")
    assert status == 200
    assert any("42" in (c.get("result") or "") for c in tools["tools"])

    # Meta / Dashboard（111）
    status, meta = _get(conn, f"/api/runs/{run_id}/meta")
    assert status == 200 and meta["status"] == "completed"
    status, runs = _get(conn, "/api/runs")
    assert any(r["run_id"] == run_id for r in runs["runs"])


def test_plan_workspace_hitl_eval_panels(client):
    conn, ws = client
    # 造一个带 plan 的 run 目录
    run_id = "plan-run-1"
    run_dir = os.path.join(ws, run_id)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as f:
        json.dump({"run_id": run_id, "status": "running", "model": "mock",
                   "input": "x"}, f)
    save_plan(run_dir, Plan(goal="g", tasks=[Task(id="T1", description="一"),
                                             Task(id="T2", description="二",
                                                  depends_on=["T1"])]))

    status, plan = _get(conn, f"/api/runs/{run_id}/plan")
    assert status == 200 and len(plan["tasks"]) == 2
    status, ws_view = _get(conn, f"/api/runs/{run_id}/workspace")
    assert status == 200 and {"run.json", "plan.json"} <= set(ws_view["files"])

    # 没有真实待审批调用的 run 不接受随意写入审批。
    status, hitl = _post(conn, "/api/hitl/decide",
                         {"run_id": run_id, "action": "approve", "payload": ""})
    assert status == 409
    assert not os.path.exists(os.path.join(run_dir, "hitl_decision.json"))

    # Eval Dashboard（119）：无报告时给出说明
    status, eval_view = _get(conn, "/api/eval")
    assert status == 200

    # 未知 run / 路径 404
    status, _ = _get(conn, "/api/runs/no-such/trace")
    assert status == 404
    status, _ = _get(conn, "/api/nope")
    assert status == 404


def _wait_view(conn, run_id, view, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status, data = _get(conn, f"/api/runs/{run_id}/{view}")
        assert status == 200
        if predicate(data):
            return data
        time.sleep(0.01)
    pytest.fail(f"等待 {view} 超时")


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_http_approval_controls_actual_execution(client, monkeypatch, action):
    from src.harness.tools.registry import ToolRegistry, ToolSpec
    from src.harness.models import factory
    from src.llm.base import ChatResult, ToolCall
    conn, ws = client
    calls = []
    registry = ToolRegistry()
    registry.register(ToolSpec("guarded", "无副作用审批测试", lambda: calls.append(1) or "executed",
                               risk_level="HIGH"))
    monkeypatch.setattr(ToolRegistry, "with_builtins", classmethod(lambda cls: registry))
    class Brain:
        model_name = "offline-approval"
        run_mode = "mock"
        def chat(self, messages, tools=None):
            if messages[-1]["role"] == "tool":
                return ChatResult(content=messages[-1]["content"])
            return ChatResult(tool_calls=[ToolCall("c1", "guarded")])
    monkeypatch.setattr(factory, "build_adapter", lambda *a, **kw: Brain())
    status, created = _post(conn, "/api/runs", {"task": "approval test"})
    run_id = created["run_id"]
    pending = _wait_view(conn, run_id, "hitl", lambda d: d["pending"])["pending"]
    assert calls == []
    assert _get(conn, f"/api/runs/{run_id}/meta")[1]["status"] == "waiting_human"
    assert _post(conn, "/api/hitl/decide", {"run_id": run_id,
        "request_id": "stale", "action": "approve"})[0] == 409
    assert _post(conn, "/api/hitl/decide", {"run_id": run_id,
        "request_id": pending["request_id"], "action": "invalid"})[0] == 400
    assert calls == []
    payload = {"run_id": run_id, "request_id": pending["request_id"], "action": action}
    assert _post(conn, "/api/hitl/decide", payload)[0] == 200
    meta = _wait_view(conn, run_id, "meta", lambda d: d["status"] == "completed")
    assert calls == ([1] if action == "approve" else [])
    assert ("executed" if action == "approve" else "permission-denied") in meta["final_text"]
    assert _post(conn, "/api/hitl/decide", payload)[0] == 409
    decision = json.loads(Path(ws, run_id, "hitl_decision.json").read_text(encoding="utf-8"))
    assert decision["action"] == action and decision["request_id"] == pending["request_id"]


def test_http_plain_answer_and_failure_are_visible(client, monkeypatch):
    from src.harness.models import factory
    from src.llm.base import ChatResult
    conn, ws = client
    class Brain:
        model_name = "offline-answer"
        run_mode = "mock"
        def chat(self, messages, tools=None):
            if messages[-1]["content"] == "fail":
                raise RuntimeError("synthetic hidden details")
            return ChatResult(content="普通回答 <img src=x onerror=void(0)>")
    monkeypatch.setattr(factory, "build_adapter", lambda *a, **kw: Brain())
    for task, expected in [("answer", "completed"), ("fail", "failed")]:
        status, created = _post(conn, "/api/runs", {"task": task})
        assert status == 200 and created["run_id"]
        meta = _wait_view(conn, created["run_id"], "meta",
                          lambda d: d["status"] in ("completed", "failed"))
        assert meta["status"] == expected
        if task == "answer":
            assert meta["final_text"] == "普通回答 <img src=x onerror=void(0)>"
        else:
            assert meta["error"] == "RuntimeError" and "synthetic hidden details" not in json.dumps(meta)


def test_concurrent_http_starts_keep_their_own_run_ids(client):
    from concurrent.futures import ThreadPoolExecutor
    conn, ws = client
    def start(task):
        other = http.client.HTTPConnection(conn.host, conn.port, timeout=10)
        try:
            status, created = _post(other, "/api/runs", {"task": task, "force_mock": True})
            assert status == 200
            meta = _wait_view(other, created["run_id"], "meta", lambda d: d["status"] == "completed")
            return created["run_id"], meta["input"]
        finally:
            other.close()
    tasks = ["并发任务甲", "并发任务乙", "并发任务丙"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(start, tasks))
    assert len({r[0] for r in results}) == 3
    assert [r[1] for r in results] == tasks


def test_expired_approval_cannot_be_replayed(tmp_path):
    from src.interfaces.web.workbench import WorkbenchState
    from src.harness.tools.registry import ToolSpec
    state = WorkbenchState(tmp_path, approval_timeout=0.1)
    spec = ToolSpec("guarded", "test", lambda: "ok", risk_level="HIGH")
    result = []
    worker = threading.Thread(target=lambda: result.append(state.wait_for_approval("r1", spec, {})))
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive() and result == [False]
    assert state.approval_view("r1") == {"pending": None}
    with pytest.raises(LookupError):
        state.decide("r1", "expired-request", "approve")
