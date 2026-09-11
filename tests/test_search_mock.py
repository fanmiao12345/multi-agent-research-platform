# -*- coding: utf-8 -*-
"""S8 模拟先行：搜索网关的 mock provider（显式标记、仅离线可选、记账字段）。"""
import pytest

from src.harness.ingest.search import (
    MockSearchInRealMode,
    SearchNotConfigured,
    SearchRecord,
    check_configured,
    ensure_provider_allowed,
    mock_search,
    search_enabled,
)


class _Settings:
    def __init__(self, provider):
        self.search_provider = provider


def test_mock_provider_is_registered_and_mock_search_is_deterministic():
    assert search_enabled("mock")
    r1 = mock_search("行业 发展", max_results=3)
    r2 = mock_search("行业 发展", max_results=3)
    assert len(r1) == 3
    assert [(x.title, x.url, x.snippet) for x in r1] == [(x.title, x.url, x.snippet) for x in r2]
    assert all(x.mock for x in r1)                      # 逐条显式标记
    assert all(x.url.startswith("https://mock.example/") for x in r1)
    assert "模拟" in r1[0].snippet


def test_mock_search_bounds_and_empty_query():
    assert mock_search("", max_results=2)[0].url.endswith("rank=1")
    assert len(mock_search("x", max_results=99)) == 10  # 上限 10
    assert len(mock_search("x", max_results=0)) == 1    # 下限 1


def test_check_configured_accepts_mock_rejects_unknown_and_empty():
    check_configured("mock", _Settings("mock"))         # mock 已注册
    with pytest.raises(SearchNotConfigured):
        check_configured("", _Settings(""))
    with pytest.raises(SearchNotConfigured):            # 真实服务商未接入前仍拒绝
        check_configured("baidu_qianfan", _Settings("baidu_qianfan"))


def test_mock_provider_forbidden_in_real_mode():
    ensure_provider_allowed("mock", run_mode="mock")    # 离线可用
    ensure_provider_allowed("", run_mode="real")        # 未配置在真实模式由 check 把关
    with pytest.raises(MockSearchInRealMode):
        ensure_provider_allowed("mock", run_mode="real")  # 红线：真实模式绝不回退 Mock


def test_search_record_carries_mock_flag():
    rec = SearchRecord(query="q", provider="mock",
                       urls=["https://mock.example/x"], mock=True)
    assert rec.to_dict()["mock"] is True
    assert SearchRecord(query="q", provider="tavily").to_dict()["mock"] is False
