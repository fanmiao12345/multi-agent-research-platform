# -*- coding: utf-8 -*-
"""D3-02/03：候选元数据、去重/限额/失败继续、正文来源接线。"""
import json

import pytest

from src.application.request import TaskRequest
from src.harness.ingest.search import RealSearchInMockMode
from tests._s4_pipeline_brain import S4Brain


class _R:
    def __init__(self, url, title="标题", snippet="摘要", rank=0,
                 published_date="", date_source="unavailable", mock=False):
        self.url = url
        self.title = title
        self.snippet = snippet
        self.rank = rank
        self.published_date = published_date
        self.date_source = date_source
        self.mock = mock


class _PlanLLM:
    model_name = "stub"
    run_mode = "mock"

    def __init__(self, queries):
        self._q = json.dumps({"queries": queries}, ensure_ascii=False)

    def chat(self, messages, tools=None):
        class _R:
            content = self._q
        return _R()


def test_auto_search_candidates_keep_metadata_dedupe_and_fail_soft():
    from src.application.web_research import auto_search_candidates

    seen_queries = []

    def fake_search(provider, query, max_results=8):
        seen_queries.append(query)
        if query.startswith("坏查询"):
            raise RuntimeError("被拦截")
        if query.startswith("查询1"):
            return [
                _R("https://a.example/1#part", title="甲", snippet="摘要甲",
                   rank=1, published_date="2026-01-02", date_source="snippet"),
                _R("https://a.example/2", title="乙", snippet="摘要乙", rank=2),
            ]
        return [
            _R("https://a.example/2", title="乙重复", snippet="摘要重复", rank=1),
            _R("https://b.example/3", title="丙", snippet="摘要丙", rank=2),
        ]

    llm = _PlanLLM(["查询1", "坏查询", "查询2"])
    calls = []
    candidates, records = auto_search_candidates(
        "主题", provider="bing_scrape", run_mode="real", llm=llm,
        site="gov.cn", since="2025-01-01", search_fn=fake_search,
        on_search=lambda **kw: calls.append(kw))
    assert seen_queries == [
        "查询1 site:gov.cn after:2025-01-01",
        "坏查询 site:gov.cn after:2025-01-01",
        "查询2 site:gov.cn after:2025-01-01",
    ]
    assert [c["url"] for c in candidates] == [
        "https://a.example/1", "https://a.example/2", "https://b.example/3"]
    assert candidates[0]["rank"] == 1 and candidates[0]["title"] == "甲"
    assert candidates[0]["snippet"] == "摘要甲"
    assert candidates[0]["published_date"] == "2026-01-02"
    assert candidates[0]["date_source"] == "snippet"
    assert len(records) == 3 and records[1]["error"] != ""
    assert calls[0]["urls"] == ["https://a.example/1", "https://a.example/2"]

    # 已知 URL（含片段）不重复抓；候选数上限仍生效
    candidates2, _ = auto_search_candidates(
        "主题", provider="bing_scrape", run_mode="real", max_candidates=1,
        known_urls={"https://a.example/1"},
        search_fn=lambda p, q, max_results=8: [
            _R("https://a.example/1#x"), _R("https://x.example/1"),
            _R("https://x.example/2")])
    assert [c["url"] for c in candidates2] == ["https://x.example/1"]


def test_auto_search_mode_gate():
    from src.application.web_research import auto_search_candidates
    with pytest.raises(RealSearchInMockMode):
        auto_search_candidates("主题", provider="bing_scrape", run_mode="mock",
                               search_fn=lambda *a, **k: [])


def test_candidates_to_sources_keep_snippet_out_of_body_and_dedupe(tmp_path, monkeypatch):
    from src.application.web_research import auto_search_candidates
    from src.application import imports as import_module
    from src.harness.ingest.fetcher import FetchResult

    search_results = [
        _R("https://a.example/one", title="甲", snippet="摘要内容不能作为正文",
           rank=1),
        _R("https://b.example/two", title="乙", snippet="另一条摘要", rank=2),
        _R("https://c.example/fail", title="失败", snippet="失败摘要", rank=3),
    ]
    candidates, _ = auto_search_candidates(
        "主题", provider="bing_scrape", run_mode="real",
        search_fn=lambda *a, **k: search_results)
    assert len(candidates) == 3

    def fake_fetch(url, policy=None):
        if url.endswith("/fail"):
            return FetchResult(status="timeout", url=url, error="超时")
        return FetchResult(
            status="ok", url=url, final_url=url, http_status=200,
            content_type="text/html", charset="utf-8",
            raw="<html><title>正文标题</title><p>真实正文内容。</p></html>".encode("utf-8"))

    monkeypatch.setattr(import_module, "fetch_url", fake_fetch)
    request = TaskRequest(task="整理资料", urls=tuple(c["url"] for c in candidates))
    store = import_module.import_request_sources(tmp_path / "job", request)
    summary = store.summary()
    assert summary["usable"] == 1
    assert summary["statuses"]["duplicate"] == 1
    assert summary["statuses"]["read_failed"] == 1
    usable = next(s for s in summary["sources"] if s["status"] == "ok")
    body = store.full_text(usable["source_id"])
    assert "真实正文内容" in body
    assert "摘要内容不能作为正文" not in body


def test_research_app_wires_web_search_failure_honestly(tmp_path):
    """mock 模式 + bing_scrape：模式闸门拦截 → web_search 失败显式入 job.json，
    链继续用给定资料完成（不假装搜过，也不中断任务）。"""
    from config.settings import Settings
    from src.application.research import ResearchApplication

    request = TaskRequest(
        task="建立资料目录：列出标题、日期与用途。",
        mode="mock", flow="research", allow_network=True,
        texts=("本材料为合成数据：试点共40人，其中32人完成问卷。",
               "本材料为合成数据：工单处理时长从10小时降至8小时。"),
        required_sections=("资料目录",))
    app = ResearchApplication(request, settings=Settings(search_provider="bing_scrape"),
                              workspace_root=tmp_path, llm=S4Brain())
    outcome = app.run()
    job = json.loads((tmp_path / "jobs" / outcome.root_job_id / "job.json")
                     .read_text(encoding="utf-8"))
    ws = job.get("web_search")
    assert ws is not None and ws["status"] == "failed"
    assert "模拟模式" in ws["error"]
    # 链没有因搜索失败而中断：来源用给定资料，任务正常收敛
    assert outcome.draft_level in ("accepted", "draft")
    sources = json.loads((tmp_path / "jobs" / outcome.root_job_id / "sources.json")
                         .read_text(encoding="utf-8"))["sources"]
    assert all("mock.example" not in s.get("url", "") for s in sources)


def test_research_app_passes_search_max_results(tmp_path, monkeypatch):
    from config.settings import Settings
    from src.application import web_research
    from src.application.research import ResearchApplication

    captured = {}

    def fake_search(topic, **kwargs):
        captured.update(kwargs)
        return [], []

    monkeypatch.setattr(web_research, "auto_search_candidates", fake_search)
    request = TaskRequest(
        task="collect", mode="mock", flow="research", allow_network=True,
        texts=("Synthetic source one.", "Synthetic source two."),
        required_sections=("Catalog",))
    app = ResearchApplication(
        request,
        settings=Settings(search_provider="mock", search_max_results=3),
        workspace_root=tmp_path, llm=S4Brain())
    app.run()
    assert captured["max_results"] == 3
