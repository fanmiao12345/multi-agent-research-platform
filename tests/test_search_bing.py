# -*- coding: utf-8 -*-
"""D3-01/02：bing_scrape 结果页抓取搜索 + 有界查询规划 + 搜索记账。"""
import json
from pathlib import Path

import pytest

from src.harness.ingest.search import (
    MockSearchInRealMode,
    RealSearchInMockMode,
    SearchError,
    SearchNotConfigured,
    bing_scrape_search,
    ensure_provider_mode,
    parse_bing_results,
    plan_queries,
    run_search,
)

FIXTURE = (Path(__file__).resolve().parent.parent / "tests" / "fixtures"
           / "bing_results.html")


class _FetchResult:
    def __init__(self, status="ok", raw=b"", error=""):
        self.status, self.raw, self.error = status, raw, error


def test_parse_bing_fixture_extracts_candidates():
    html = FIXTURE.read_text(encoding="utf-8")
    results = parse_bing_results(html, max_results=10)
    assert len(results) >= 5                       # 夹具有 10 条 b_algo，至少解析出 5
    for index, r in enumerate(results, start=1):
        assert r.url.startswith(("http://", "https://"))
        assert r.title and r.mock is False         # 真实结果，非模拟
        assert r.rank == index
    assert any(r.snippet for r in results)
    assert results[0].published_date == "2026-05-29"
    assert results[0].date_source == "snippet"


def test_bing_scrape_search_with_injected_fetch():
    html = FIXTURE.read_text(encoding="utf-8")
    seen = {}
    def fake_fetch(url):
        seen["url"] = url
        return _FetchResult(raw=html.encode("utf-8"))
    results = bing_scrape_search("研究写作", max_results=5, fetch=fake_fetch)
    assert len(results) == 5
    assert "cn.bing.com/search?q=" in seen["url"] and seen["url"].endswith(
        "%E7%A0%94%E7%A9%B6%E5%86%99%E4%BD%9C")


def test_bing_scrape_blocked_page_raises_not_fakes():
    def fake_fetch(url):
        return _FetchResult(raw="<html>百度安全验证</html>".encode("utf-8"))
    with pytest.raises(SearchError, match="未包含结果条目"):
        bing_scrape_search("x", fetch=fake_fetch)

    def bad_status(url):
        return _FetchResult(status="timeout", error="超时")
    with pytest.raises(SearchError, match="抓取失败"):
        bing_scrape_search("x", fetch=bad_status)

    def empty_results(url):
        return _FetchResult(raw=b"<ol id='b_results'><li class='b_algo'><h2>x</h2></li></ol>")
    with pytest.raises(SearchError, match="0 条候选"):
        bing_scrape_search("x", fetch=empty_results)


def test_mode_gates_and_dispatcher():
    with pytest.raises(RealSearchInMockMode):
        ensure_provider_mode("bing_scrape", run_mode="mock")
    with pytest.raises(MockSearchInRealMode):
        ensure_provider_mode("mock", run_mode="real")
    assert ensure_provider_mode("bing_scrape", run_mode="real") is None
    # mock 模式跑 mock 提供方：离线确定性结果
    results = run_search("mock", "查询", max_results=3)
    assert len(results) == 3 and all(r.mock for r in results)
    with pytest.raises(SearchNotConfigured):
        run_search("baidu_api", "查询")


def test_plan_queries_bounded_and_fallback():
    class _FakeLLM:
        model_name = "stub"
        run_mode = "mock"

        def __init__(self, content):
            self._content = content

        def chat(self, messages, tools=None):
            class _R:
                content = self._content
            return _R()

    # 无模型：退回主题本身
    assert plan_queries("某行业现状", llm=None) == ["某行业现状"]
    # 模型给出 3 个查询：去重、有界
    llm = _FakeLLM(json.dumps({"queries": ["A 行业规模", "A 行业规模", "B 政策", "C 案例"]},
                              ensure_ascii=False))
    assert plan_queries("主题", llm=llm, max_queries=4) == ["A 行业规模", "B 政策", "C 案例"]
    # 模型输出垃圾：退回主题
    assert plan_queries("主题", llm=_FakeLLM("不是JSON")) == ["主题"]
    # 上限 4
    many = _FakeLLM(json.dumps({"queries": ["1", "2", "3", "4", "5", "6"]}))
    assert len(plan_queries("主题", llm=many)) == 4
    # D3-02：站点/起始日期条件进入每个查询；无效日期显式拒绝
    assert plan_queries("主题", site="gov.cn", since="2025-01-01") == [
        "主题 site:gov.cn after:2025-01-01"]
    assert plan_queries("主题", llm=llm, site="gov.cn", since="2025-01-01")[0] == (
        "A 行业规模 site:gov.cn after:2025-01-01")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        plan_queries("主题", since="2025/01/01")


def test_ledger_record_search_entry(tmp_path):
    """D3-01：搜索调用入根账本（kind=search，不计模型调用次数）。"""
    from src.harness.model_gateway import JobLedger
    from tests.test_orchestration_s8 import _req
    root = "job_" + "9" * 32
    ledger = JobLedger(tmp_path / "jobs" / root, _req())
    ledger.record_search(provider="bing_scrape", query="研究写作",
                         urls=["https://a.example/x"], elapsed_seconds=1.2,
                         cost_usd=0.0)
    ledger.record_search(provider="bing_scrape", query="坏查询", error="被拦截")
    summary = ledger.summary()
    assert summary["call_count"] == 0                 # 搜索不计模型调用次数
    searches = [c for c in summary["calls"] if c.get("kind") == "search"]
    assert len(searches) == 2
    assert searches[0]["status"] == "completed" and searches[0]["urls"]
    assert searches[1]["status"] == "failed" and "拦截" in searches[1]["error"]
