# -*- coding: utf-8 -*-
"""O-14 B 方案（站点降权换源）与 O-15 就绪开关（历史窗口）的单元测试。"""
from src.harness.ingest.site_policy import (DEFAULT_BLOCKED_DOMAINS,
                                            DomainBlocklist, build_domain_blocklist,
                                            domain_matches, domain_of,
                                            parse_blocked_domains)
from src.harness.ingest.search import filter_search_results


class _Result:
    """搜索候选桩（duck-typing，与 bing_scrape 的结果对象同字段）。"""

    def __init__(self, title, url, snippet="正文关键词内容"):
        self.title = title
        self.url = url
        self.snippet = snippet


# ---- 域名工具 ----

def test_domain_of_strips_www_and_port():
    assert domain_of("https://zhuanlan.zhihu.com/p/123") == "zhuanlan.zhihu.com"
    assert domain_of("https://www.zhihu.com:443/question/1") == "zhihu.com"
    assert domain_of("不是链接") == ""


def test_domain_matches_subdomain():
    assert domain_matches("https://zhuanlan.zhihu.com/p/1", "zhihu.com")
    assert domain_matches("https://zhihu.com/x", "zhihu.com")
    assert not domain_matches("https://notzhihu.com/x", "zhihu.com")
    assert not domain_matches("https://example.com/", "zhihu.com")


def test_parse_blocked_domains_env_semantics():
    assert parse_blocked_domains("a.com, b.com") == ("a.com", "b.com")
    assert parse_blocked_domains("none") == ()
    assert parse_blocked_domains("") == ()


def test_blocklist_static_dynamic_and_403_only():
    blocklist = DomainBlocklist(static=("zhihu.com",))
    assert blocklist.blocks("https://www.zhihu.com/q")
    assert not blocklist.blocks("https://example.com/x")
    # 只有 403 记入动态名单；500/超时不记
    assert blocklist.record_rejection("https://blog.example.com/a", 403) is True
    assert blocklist.record_rejection("https://slow.example.com/b", 500) is False
    assert blocklist.record_rejection("https://x.example.com/c", None) is False
    assert blocklist.blocks("https://blog.example.com/d")
    # 证据基线默认名单含实测全线 403 的站点
    assert "baike.baidu.com" in DEFAULT_BLOCKED_DOMAINS


def test_build_blocklist_from_settings_override():
    class _S:
        search_blocked_domains = ("example.com",)

    assert build_domain_blocklist(_S()).all_domains() == ("example.com",)

    class _Empty:
        search_blocked_domains = ()

    assert build_domain_blocklist(_Empty()).static == DEFAULT_BLOCKED_DOMAINS


# ---- 过滤器接线：blocked 域名直接丢弃、不参与安全阀 ----

def _query():  # 关键词重合度达标的最小查询
    return "动力电池 回收 政策"


def test_filter_drops_blocked_domain_with_reason():
    results = [_Result("动力电池回收政策解读", "https://baike.baidu.com/item/动力电池"),
               _Result("动力电池回收政策白皮书", "https://example.org/wp")]
    kept, dropped = filter_search_results(_query(), results,
                                          blocked_domains=frozenset({"baidu.com"}))
    assert [r.url for r in kept] == ["https://example.org/wp"]
    assert len(dropped) == 1 and "403" in dropped[0]["reason"]


def test_filter_blocked_not_rescued_by_fallback():
    results = [_Result("动力电池回收政策报告", "https://zhihu.com/answer/1")]
    kept, dropped = filter_search_results(_query(), results,
                                          fallback_keep=2,
                                          blocked_domains=frozenset({"zhihu.com"}))
    assert kept == []                       # 安全阀不救 403 站点
    assert dropped and dropped[0]["reason"].startswith("站点已知")


def test_filter_without_blocklist_unchanged():
    results = [_Result("动力电池回收政策解读", "https://example.org/a")]
    kept, dropped = filter_search_results(_query(), results)
    assert len(kept) == 1 and dropped == []


# ---- O-15 就绪开关：默认行为不变，开启后历史窗口恢复 ----

def _sources():
    from src.harness.context.policy import ContextSource
    return [ContextSource(kind="instructions", content="指令"),
            ContextSource(kind="memory", content="记忆")]


def test_compose_context_default_history_window_is_two():
    from src.harness.context.builder import compose_context

    history = [{"role": "user", "content": f"旧消息{i}"} for i in range(12)]
    messages, _ = compose_context("问题", _sources(), history=list(history))
    # 旧行为（O-15）：归一化挤占 messages 份额 → 历史只保留最近 2 条
    history_in_prompt = [m for m in messages[1:] if m.get("role") == "user"
                         and m.get("content") in {h["content"] for h in history}]
    assert len(history_in_prompt) == 2


def test_compose_context_reserve_window_keeps_more_history():
    from src.harness.context.builder import compose_context

    history = [{"role": "user", "content": f"旧消息{i}"} for i in range(12)]
    messages, _ = compose_context("问题", _sources(), history=list(history),
                                  reserve_message_window=True)
    history_in_prompt = [m for m in messages[1:] if m.get("role") == "user"
                         and m.get("content") in {h["content"] for h in history}]
    assert len(history_in_prompt) > 2       # 窗口恢复到与预算成比例


def test_runtime_context_carries_o15_switch():
    from src.harness.runtime.run_context import RuntimeContext

    base = RuntimeContext.from_settings()
    assert base.reserve_message_window is False          # 默认关
    assert base.with_updates(reserve_message_window=True) \
        .reserve_message_window is True
