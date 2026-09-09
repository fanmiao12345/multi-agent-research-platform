# -*- coding: utf-8 -*-
"""测试：HTML 正文提取与 URL 抓取器（S2-02/09 / B4-02）。"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.harness.ingest.fetcher import fetch_url
from src.harness.ingest.html_extract import extract_document, extract_html
from src.harness.ingest.url_policy import UrlPolicy


def _server(routes):
    """routes: dict[path] -> (status, headers, body_bytes)；返回 (origin, stop)。"""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            route = routes.get(self.path)
            if route is None:
                self.send_response(404)
                self.end_headers()
                return
            status, headers, body = route
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return f"http://127.0.0.1:{port}", server


@pytest.fixture()
def policy():
    return UrlPolicy(allowed_hosts={"127.0.0.1"})


def _stop(server):
    server.shutdown()
    server.server_close()


def test_extract_html_title_paragraphs_headings_and_skips_scripts():
    html = """<html><head><title>样本标题</title></head><body>
    <script>alert('xss')</script>
    <style>.a{}</style>
    <h1>第一章</h1>
    <p>正文段落一。</p>
    <p>正文段落二。<br>换行仍在段落二。</p>
    <h2>小节</h2>
    <div>块内容。</div></body></html>"""
    result = extract_html(html)
    assert result["title"] == "样本标题"
    assert result["format"] == "md"
    assert "# 第一章" in result["text"]
    assert "正文段落一。" in result["text"]
    assert "正文段落二。" in result["text"] and "换行仍在段落二。" in result["text"]
    assert "## 小节" in result["text"]
    assert "alert" not in result["text"] and ".a{}" not in result["text"]
    assert "script" not in result["text"].lower()


def test_extract_document_by_content_type():
    plain = extract_document("纯文本一行。\n\n第二段。".encode("utf-8"), "text/plain")
    assert plain["format"] == "txt" and "第二段" in plain["text"]
    md = extract_document("# 文档\n\n正文。".encode("utf-8"), "text/markdown")
    assert md["format"] == "md" and md["title"] == "文档"
    html = extract_document("<title>T</title><p>正文</p>".encode("utf-8"), "text/html")
    assert html["title"] == "T" and "正文" in html["text"]
    # 无内容类型时嗅探
    sniffed = extract_document(b"<p>x</p>", "")
    assert sniffed["format"] == "md"
    # GB18030 声明
    gbk = extract_document("中文正文。".encode("gb18030"), "text/plain; charset=gb18030")
    assert "中文正文" in gbk["text"]


def test_fetch_ok_and_html(tmp_path, policy):
    body = ("<title>抓取测试</title><h1>标题甲</h1><p>网页正文。</p>").encode("utf-8")
    origin, server = _server({"/page": (200, {"Content-Type": "text/html; charset=utf-8"}, body)})
    try:
        result = fetch_url(origin + "/page", policy)
        assert result.status == "ok" and result.http_status == 200
        assert result.final_url == origin + "/page"
        assert result.content_type.startswith("text/html")
        extracted = extract_document(result.raw, result.content_type)
        assert extracted["title"] == "抓取测试" and "网页正文" in extracted["text"]
    finally:
        _stop(server)


def test_fetch_redirects_rechecked_each_hop(policy):
    origin, server = _server({
        "/a": (302, {"Location": "/b"}, b""),
        "/b": (301, {"Location": "http://127.0.0.1:1/trap"}, b""),  # 非法端口跳点
    })
    try:
        result = fetch_url(origin + "/a", policy)
        # /b 的 Location 指向 127.0.0.1:1：逐跳重检地址（白名单内）通过，
        # 但连接必然失败 → network_error（不会去读外部/私网内容）
        assert result.status == "network_error"
        assert result.final_url == "http://127.0.0.1:1/trap"
    finally:
        _stop(server)


def test_fetch_redirect_limit(policy):
    origin, server = _server({"/loop": (302, {"Location": "/loop"}, b"")})
    try:
        result = fetch_url(origin + "/loop", UrlPolicy(allowed_hosts={"127.0.0.1"}, max_redirects=2))
        assert result.status == "redirect_limit"
    finally:
        _stop(server)


def test_fetch_http_error_and_unsupported_type(policy):
    origin, server = _server({
        "/missing": (404, {"Content-Type": "text/plain"}, b"nope"),
        "/image.png": (200, {"Content-Type": "image/png"}, b"\x89PNG\r\n\x1a\n"),
        "/ok.txt": (200, {"Content-Type": "text/plain"}, "普通文本。".encode("utf-8")),
    })
    try:
        result = fetch_url(origin + "/missing", policy)
        assert result.status == "http_error" and result.http_status == 404
        result = fetch_url(origin + "/image.png", policy)
        assert result.status == "unsupported_type"
        assert "image/png" in result.error
        result = fetch_url(origin + "/ok.txt", policy)
        assert result.status == "ok"
        assert extract_document(result.raw, result.content_type)["text"] == "普通文本。"
    finally:
        _stop(server)


def test_fetch_oversize_stops(policy):
    origin, server = _server({
        "/big": (200, {"Content-Type": "text/plain"}, b"x" * (64 * 1024 + 7)),
    })
    try:
        small = UrlPolicy(allowed_hosts={"127.0.0.1"}, max_download_bytes=1024)
        result = fetch_url(origin + "/big", small)
        assert result.status == "too_large"
    finally:
        _stop(server)


def test_fetch_private_address_blocked_without_allowlist(policy):
    origin, server = _server({"/x": (200, {"Content-Type": "text/plain"}, b"ok")})
    try:
        # 默认策略不允许回环：明确失败而不是抓取
        result = fetch_url(origin + "/x", UrlPolicy())
        assert result.status == "network_error"
        assert "不允许的地址" in result.error
    finally:
        _stop(server)


def test_fetch_scheme_and_dns_errors(policy):
    assert fetch_url("ftp://example.com/x", policy).status == "network_error"
    result = fetch_url("http://no-such-host.invalid/x", policy)
    assert result.status == "network_error"
