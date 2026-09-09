# -*- coding: utf-8 -*-
"""测试：SourceStore 登记 URL 来源（S2-05/06 URL 侧 / B4-03）。"""
import pytest

from src.harness.storage.sources import SourceStore


def _store(tmp_path):
    return SourceStore(tmp_path / "job")


def test_add_url_ok_records_final_url_http_and_fulltext(tmp_path):
    store = _store(tmp_path)
    record = store.add_url(
        url="https://example.com/a?q=1", final_url="https://example.com/b",
        http_status=200, content_type="text/html; charset=utf-8",
        title="网页标题甲", text="# 第一节\n\n网页正文段落。", status="ok")
    assert record.status == "ok" and record.kind == "url"
    assert record.title == "网页标题甲"
    assert record.final_url == "https://example.com/b"
    assert record.http_status == 200
    assert record.published_date == "unknown"
    summary = store.summary()
    entry = summary["sources"][0]
    assert entry["final_url"] == "https://example.com/b"
    assert entry["content_type"].startswith("text/html")
    assert entry["segment_count"] == 1
    assert store.full_text(record.source_id) == "# 第一节\n\n网页正文段落。"


def test_add_url_redirect_keeps_original_address(tmp_path):
    store = _store(tmp_path)
    record = store.add_url(url="https://a.example/x", final_url="https://b.example/y",
                           http_status=200, content_type="text/plain",
                           text="重定向后的正文。", status="ok")
    assert record.original_address == "https://a.example/x"
    assert record.final_url == "https://b.example/y"


def test_add_url_failure_statuses_are_registered(tmp_path):
    store = _store(tmp_path)
    store.add_url(url="http://x.example/missing", http_status=404,
                  content_type="text/plain", status="http_error", message="HTTP 404")
    store.add_url(url="http://x.example/img", http_status=200,
                  content_type="image/png", status="unsupported_type",
                  message="内容类型不受支持：image/png")
    store.add_url(url="http://x.example/time", status="timeout",
                  message="连接或读取超时")
    summary = store.summary()
    assert summary["usable"] == 0 and summary["total"] == 3
    # 抓取层状态映射到存储层：http_error/timeout→read_failed，unsupported_type→unsupported
    assert summary["statuses"].get("read_failed") == 2
    assert summary["statuses"].get("unsupported") == 1
    assert all(s["status_message"] for s in summary["sources"])
    assert all(store.full_text(s["source_id"]) is None for s in summary["sources"])


def test_add_url_empty_and_oversize(tmp_path):
    store = _store(tmp_path)
    store.add_url(url="http://x.example/empty", http_status=200,
                  content_type="text/html", text="   ", status="ok")
    record = store.add_url(url="http://x.example/big", http_status=200,
                           content_type="text/plain", text="x" * (2 * 1024 * 1024 + 1),
                           status="ok")
    assert record.status == "too_large"
    summary = store.summary()
    assert summary["statuses"]["empty"] == 1
    assert summary["statuses"]["too_large"] == 1
    assert summary["usable"] == 0


def test_add_url_duplicates_local_and_web_content(tmp_path):
    store = _store(tmp_path)
    store.add_paste("同一份正文内容。", display_index=1)
    record = store.add_url(url="http://x.example/repost", http_status=200,
                           content_type="text/html",
                           text="同一份正文内容。", status="ok")
    assert record.status == "duplicate" and record.duplicate_of is not None
    assert store.full_text(record.source_id) is None
    summary = store.summary()
    assert summary["usable"] == 1 and summary["statuses"]["duplicate"] == 1


def test_add_url_content_format_md_txt_and_partial(tmp_path):
    store = _store(tmp_path)
    md = store.add_url(url="http://x.example/md", http_status=200,
                       content_type="text/markdown", text="# 标题\n\n正文。",
                       status="ok", content_format="md")
    assert md.content_format == "md"
    txt = store.add_url(url="http://x.example/txt", http_status=200,
                        content_type="text/plain", text="普通段落。",
                        status="ok", content_format="txt")
    assert txt.content_format == "txt"
    partial = store.add_url(url="http://x.example/partial", http_status=200,
                            content_type="text/html",
                            text="部分解码的正文。", status="partial",
                            message="部分字节无法解码")
    assert partial.status == "partial" and partial.file_name
    summary = store.summary()
    assert summary["usable"] == 3
    assert any(s["status"] == "partial" for s in summary["sources"])
