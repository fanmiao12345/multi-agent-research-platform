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
    filter_search_results,
    parse_bing_results,
    plan_queries,
    query_terms,
    result_relevance,
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


def test_result_relevance_filter_drops_lexicon_and_calendar_pages():
    """D3-03 相关性过滤：用联网批（Q2-02 真基线）里实测的查询/标题对做回归。

    修复前 12 题里 6 题因这类页面挤满候选而只能 unable。
    """
    class _R:
        def __init__(self, title, snippet=""):
            self.title = title
            self.snippet = snippet
            self.url = "https://example/" + str(abs(hash(title)) % 10_000)

    # 查询关键词：短查询口径；泛词（研究/现状）不进关键词表
    assert "研究" not in query_terms("远程办公 团队协作 研究")
    assert "site:gov.cn" not in query_terms("数据出境 合规 site:gov.cn")

    query = "远程办公 团队协作 实证研究"
    junk = [_R("混合（汉语词语）_百度百科"),
            _R("混合 | 简体中文-英语翻译——剑桥词典"),
            _R("ToDesk远程桌面软件-免费安全流畅的远程连接电脑手机"),
            _R("混合的意思,混合的拼音、近义词、反义词、造句 - 汉语查")]
    good = _R("混合办公对团队协作影响的实证研究：一项追踪调查",
              "样本包含 120 个团队，比较远程、混合与现场办公的协作指标。")
    kept, dropped = filter_search_results(query, junk + [good])
    assert [r.title for r in kept] == [good.title]
    assert len(dropped) == 4
    assert any("词典" in d["reason"] for d in dropped)
    assert result_relevance(query, good.title, good.snippet) > result_relevance(
        query, junk[2].title, junk[2].snippet)

    # 日历页：与主题无关时丢弃；查询本身要日历时不再按"日历页"规则丢弃
    calendar = [_R("2026年日历全年完整图_带农历节假日放假安排"), _R("2026年大事、要事、重要节日一览表")]
    kept_cal, dropped_cal = filter_search_results("2026 人工智能 监管 争议", calendar)
    assert kept_cal == [] and len(dropped_cal) == 2
    kept_want, dropped_want = filter_search_results("2026 年放假安排 日历", calendar)
    assert calendar[0] in kept_want
    assert not any("日历/节假日页" in d["reason"] for d in dropped_want)

    # 安全阀：整组都不达标时保留重合度最高的少数条目（只救"重合不足"，不救日历/词典页）
    fallback_input = [_R("新浪网 404 Not Found"), _R("新片场 - 与百万创作人一起成长")]
    kept_fb, dropped_fb = filter_search_results("动力电池回收 监管政策", fallback_input,
                                                fallback_keep=1)
    assert len(kept_fb) == 1 and any(d.get("fallback") for d in dropped_fb)
    kept_fb2, _ = filter_search_results("动力电池回收 监管政策", fallback_input)
    assert len(kept_fb2) == 2                 # 默认安全阀保留 2 条
    kept_cal_fb, _ = filter_search_results("动力电池回收 监管政策",
                                           [_R("2026年日历全年完整图_带农历节假日放假安排")])
    assert kept_cal_fb == []          # 日历页即使一条不剩也不进安全阀


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
