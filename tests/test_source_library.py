# -*- coding: utf-8 -*-
"""D3-05：来源版本/撤回/过期/下游引用与根任务共享来源库。"""
import json

from src.application.orchestration import OrchestrationExecutor
from src.application.request import TaskRequest
from src.harness.ingest.fetcher import FetchResult
from src.harness.storage.sources import SourceStore
from tests.test_orchestration_s8 import (CAPS, _executor_with, _fanout_plan_dict,
                                         _req, from_plan_dict)


def test_source_version_expiry_and_withdrawal_find_dependents(tmp_path):
    job = tmp_path / "jobs" / "job_root"
    store = SourceStore(job)
    first = store.add_paste("第一版正文。", display_index=1)
    (job / "evidence.json").write_text(json.dumps({"items": [
        {"evidence_id": "E-001", "source_id": first.source_id,
         "quote": "第一版正文。"}]}, ensure_ascii=False), encoding="utf-8")

    second = store.add_version(first.source_id, "第二版正文。", title="新版来源")
    records = {r["source_id"]: r for r in store.summary()["sources"]}
    assert records[first.source_id]["status"] == "superseded"
    assert records[first.source_id]["superseded_by"] == second.source_id
    versioned = records[second.source_id]
    assert versioned["source_version"] == 2
    assert versioned["root_source_id"] == first.source_id
    assert versioned["supersedes"] == first.source_id

    store.set_expiry(second.source_id, "2026-01-01T00:00:00")
    changed = store.expire_due(now="2026-01-02T00:00:00")
    assert changed[0]["status"] == "expired"
    assert store.full_text(second.source_id) is not None  # 全文保留，但 summary 不再 usable
    assert store.summary()["usable"] == 0

    active = store.add_paste("仍在使用并需撤回的来源。", display_index=2)
    (job / "report.json").write_text(json.dumps(
        {"source_id": active.source_id}), encoding="utf-8")
    result = store.withdraw(active.source_id, "来源方撤回")
    assert result["source"]["status"] == "withdrawn"
    assert any(item["path"].endswith("report.json") for item in result["dependents"])


def test_shared_library_links_versions_without_copying_text(tmp_path):
    job = tmp_path / "jobs" / "job_root"
    root = SourceStore(job / "shared_sources")
    root_record = root.add_paste("共享正文。", display_index=1)
    child = SourceStore(job / "child")
    child_record = child.add_paste("共享正文。", display_index=1)

    linked = child.link_source_library(root, source_job_id="job_root")
    assert linked == 1
    record = child._load_record(child_record.source_id)
    assert record.root_source_id == root_record.source_id
    assert record.source_job_id == "job_root"
    assert record.source_version == 1
    assert record.retrieved_at


def test_fanout_downloads_root_sources_once_and_children_reuse_text(tmp_path, monkeypatch):
    from src.application import imports as import_module

    calls = []

    def fake_fetch(url, policy=None):
        calls.append(url)
        return FetchResult(
            status="ok", url=url, final_url=url, http_status=200,
            content_type="text/html", charset="utf-8",
            raw="<html><title>共享网页</title><p>网页正文证据。</p></html>".encode("utf-8"))

    monkeypatch.setattr(import_module, "fetch_url", fake_fetch)
    request = TaskRequest(
        task="研究主题", mode="mock", urls=("https://example.com/shared",),
        allow_network=True, max_cost=0.30, max_seconds=300)
    root = "job_" + "e" * 32

    def script(req):
        if "子题一" in req.task:
            return ("job_s1", "子题一小节", "accepted")
        if "子题二" in req.task:
            return ("job_s2", "子题二小节", "accepted")
        return (root, "最终报告", "accepted")

    ex, requests, _ = _executor_with(script, workspace_root=tmp_path)
    plan = from_plan_dict(_fanout_plan_dict())
    record = ex.execute_plan(request, plan, budget_caps=CAPS, root_job_id=root)
    assert record["draft_level"] == "accepted"
    assert calls == ["https://example.com/shared"]
    assert all(req.urls == () for req in requests)
    assert all(not req.allow_network for req in requests)
    assert all(any("网页正文证据" in text for text in req.texts) for req in requests)
    manifest = json.loads((tmp_path / "jobs" / root / "source_library.json")
                          .read_text(encoding="utf-8"))
    assert manifest["retrieved_once"] is True
    assert manifest["sources"][0]["status"] == "ok"