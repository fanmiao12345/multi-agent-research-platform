# -*- coding: utf-8 -*-
"""
application/web_research.py —— D3-02/03 自动联网研究编排。

流程：有界查询规划（可附站点/起始日期条件）→ 逐查询搜索 → 保存候选元数据并按
URL 去重/限额 → 候选 URL 交给既有来源导入管线读取正文/登记/按正文去重。
搜索摘要只留在候选记录；只有抓取成功的正文才进入来源证据。

失败语义：单个查询失败记录后继续下一查询；模式闸门/未配置/全部查询失败显式呈现，
不假装搜过。每次搜索调用经回调入根账本（kind=search）。
"""
from __future__ import annotations

import time
from urllib.parse import urldefrag

from src.harness.ingest.search import (
    SUPPORTED_PROVIDERS,
    SearchNotConfigured,
    ensure_provider_mode,
    filter_search_results,
    plan_queries,
    run_search,
)


def _normalize_url(url: str) -> str:
    """候选 URL 去重口径：去掉片段，保留查询参数与原始大小写之外的必要内容。"""
    clean = (url or "").strip()
    if not clean:
        return ""
    return urldefrag(clean)[0].strip()


def _candidate_from_result(result, *, provider: str, query: str,
                           position: int) -> dict:
    url = _normalize_url(getattr(result, "url", "") or "")
    snippet = str(getattr(result, "snippet", "") or "").strip()
    published_date = str(getattr(result, "published_date", "") or "").strip()
    date_source = str(getattr(result, "date_source", "") or "").strip()
    if published_date and not date_source:
        date_source = "search_result"
    if not published_date and not date_source:
        date_source = "unavailable"
    try:
        rank = int(getattr(result, "rank", 0) or position)
    except (TypeError, ValueError):
        rank = position
    return {
        "provider": provider,
        "query": query,
        "rank": rank,
        "title": str(getattr(result, "title", "") or "").strip(),
        "url": url,
        "snippet": snippet,
        "published_date": published_date,
        "date_source": date_source,
        "mock": bool(getattr(result, "mock", False)),
    }


def auto_search_candidates(topic: str, *, provider: str, run_mode: str = "real",
                           llm=None, max_queries: int = 4, max_results: int = 8,
                           max_candidates: int = 8, known_urls: set | None = None,
                           site: str = "", since: str = "", search_fn=None,
                           on_search=None) -> tuple[list[dict], list[dict]]:
    """返回 (候选元数据列表, 搜索记录列表)；候选列表交给 URL 抓取与根账本。"""
    provider_name = (provider or "").strip().lower()
    if provider_name not in SUPPORTED_PROVIDERS:
        raise SearchNotConfigured(f"搜索服务 {provider or '未配置'} 尚未接入")
    ensure_provider_mode(provider_name, run_mode)
    if max_candidates <= 0:
        return [], []
    search = search_fn or run_search
    known = {_normalize_url(url) for url in (known_urls or ()) if _normalize_url(url)}
    queries = plan_queries(topic, llm, max_queries=max_queries,
                           site=site, since=since)
    seen = set(known)
    candidates: list[dict] = []
    records: list[dict] = []
    for query in queries:
        started = time.monotonic()
        error = ""
        query_candidates: list[dict] = []
        dropped: list[dict] = []
        try:
            results = search(provider_name, query, max_results=max_results)
            # Q3-02 联网专项：结果先按关键词重合度与页面类型过滤，词典/日历/下载页
            # 不再占用候选名额（实测这类页面挤满候选是 6/12 题 unable 的直接原因）。
            results, dropped = filter_search_results(query, results)
            for position, result in enumerate(results, start=1):
                item = _candidate_from_result(
                    result, provider=provider_name, query=query, position=position)
                if not item["url"] or item["url"] in seen:
                    continue
                seen.add(item["url"])
                query_candidates.append(item)
                if len(candidates) + len(query_candidates) >= max_candidates:
                    break
        except Exception as e:  # noqa: BLE001 —— 单查询失败记录后继续下一查询
            error = f"{type(e).__name__}: {e}"
        elapsed = time.monotonic() - started
        urls = [item["url"] for item in query_candidates]
        record = {
            "provider": provider_name, "query": query, "urls": urls,
            "candidates": query_candidates, "dropped": dropped,
            "elapsed_seconds": round(elapsed, 3), "error": error,
        }
        records.append(record)
        if on_search is not None:
            on_search(provider=provider_name, query=query, urls=urls,
                      elapsed_seconds=record["elapsed_seconds"], error=error)
        candidates.extend(query_candidates)
        if len(candidates) >= max_candidates:
            break
    return candidates[:max_candidates], records
