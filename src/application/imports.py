
# -*- coding: utf-8 -*-
"""资料导入总入口：本地文本/文件 + 用户指定 URL（S2-01~09，批次B4）。

ResearchApplication 与 Web/CLI/评测共用本入口；URL 抓取按 UrlPolicy 白名单执行，
失败逐条登记并汇总；零可用来源整体失败，不带着“假资料”运行。
"""
from __future__ import annotations

from src.harness.ingest.fetcher import fetch_url
from src.harness.ingest.html_extract import extract_document
from src.harness.ingest.url_policy import UrlPolicy
from src.harness.storage.sources import (MAX_SOURCES, SourceImportError,
                                         SourceStore, check_total_limit)


def import_request_sources(job_dir, request, url_policy: UrlPolicy | None = None) -> SourceStore:
    """把一次请求携带的资料（texts/files/urls）全部导入 job_dir。

    返回已登记全部条目的 SourceStore；一个可用来源都没有时抛 SourceImportError
    （消息带全部失败条目），部分失败保留索引不静默。
    """
    total = len(request.texts) + len(request.files) + len(request.urls)
    if total > MAX_SOURCES:
        raise SourceImportError(
            f"资料超过单任务 {MAX_SOURCES} 个来源的上限，请缩小导入范围")
    policy = url_policy or UrlPolicy()
    store = SourceStore(job_dir)
    for index, text in enumerate(request.texts, start=1):
        store.add_paste(text, display_index=index)
        check_total_limit(store)
    for path in request.files:
        store.add_file(path)
        check_total_limit(store)
    for url in request.urls:
        _import_url(store, url, policy)
        check_total_limit(store)
    summary = store.summary()
    if summary["usable"] == 0:
        notes = []
        for record in summary["sources"]:
            label = record.get("display") or record.get("source_id")
            if record["status"] == "duplicate":
                notes.append(f"{label}：{record.get('status_message')}")
            else:
                notes.append(f"{label}：{record.get('status_message') or record['status']}")
        detail = "；".join(notes) if notes else "没有可用资料"
        raise SourceImportError(f"导入后没有可用资料：{detail}")
    return store


def _import_url(store: SourceStore, url: str, policy: UrlPolicy) -> None:
    """抓取一个用户指定 URL 并登记来源（fetch→正文提取→add_url）。"""
    result = fetch_url(url, policy)
    if result.status != "ok":
        store.add_url(url=url, http_status=result.http_status,
                      content_type=result.content_type, status=result.status,
                      message=result.error or result.status)
        return
    extracted = extract_document(result.raw or b"", result.content_type, result.charset)
    store.add_url(url=url, final_url=result.final_url,
                  http_status=result.http_status, content_type=result.content_type,
                  encoding=extracted.get("encoding", ""),
                  title=extracted.get("title", ""), text=extracted.get("text", ""),
                  content_format=extracted.get("format", "txt"), status="ok")
