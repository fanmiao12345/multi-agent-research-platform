# -*- coding: utf-8 -*-
"""
harness/ingest/fetcher.py —— URL 抓取器（S2-02/09）

- 使用 http.client 直连"解析白名单校验后的 IP"，每跳重新 parse+resolve；
- HTTPS 通过 server_hostname 建立 TLS（SNI/证书校验针对原始主机名）；
- 限制：连接/读取超时、下载字节上限（超限即断）、只读正文类 Content-Type；
- 状态分类：ok / http_error / timeout / network_error / too_large /
  unsupported_type / redirect_limit —— 供来源登记与用户提示使用。
"""
from __future__ import annotations

import http.client
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urljoin

from src.harness.ingest.url_policy import (UrlPolicy, UrlPolicyError, ParsedUrl,
                                           parse_url, resolve_allowed)

_ACCEPTABLE_TYPES = {
    "text/html", "application/xhtml+xml", "text/plain",
    "text/markdown", "application/markdown",
}
_TIMEOUT_NAMES = {socket.timeout: "timeout", TimeoutError: "timeout"}


@dataclass
class FetchResult:
    status: str                    # ok | http_error | timeout | network_error | too_large | unsupported_type | redirect_limit
    url: str = ""
    final_url: str = ""
    http_status: int | None = None
    content_type: str = ""
    charset: str = ""
    raw: bytes | None = None
    headers: dict = None
    error: str = ""
    elapsed_seconds: float = 0.0


def _pinned_connection(parsed: ParsedUrl, ip: str, policy: UrlPolicy,
                       timeout: float):
    """直连指定 IP，HTTPS 用 server_hostname 保留证书校验。"""
    sock = socket.create_connection((ip, parsed.port), timeout=timeout)
    sock.settimeout(timeout)
    if parsed.scheme == "https":
        context = ssl.create_default_context()
        sock = context.wrap_socket(sock, server_hostname=parsed.hostname)
        conn = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=timeout)
    else:
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    conn.sock = sock
    return conn


def fetch_url(url: str, policy: UrlPolicy | None = None,
              resolver=socket.getaddrinfo) -> FetchResult:
    """抓取一个 URL（重定向逐跳复检）。仅测试/可信场景放宽地址白名单。"""
    started = time.monotonic()
    policy = policy or UrlPolicy()
    result = FetchResult(status="ok")
    current = url
    result.url = url
    try:
        for hop in range(policy.max_redirects + 1):
            parsed = parse_url(current, policy=policy)
            ips = resolve_allowed(parsed, policy, resolver=resolver)
            outcome = _request_once(parsed, ips, policy)
            if outcome.status == "redirect":
                current = urljoin(current, outcome.error)
                continue
            if outcome.status == "http_error":
                result.status = "http_error"
                result.http_status = outcome.http_status
                result.final_url = current
                result.error = f"HTTP {outcome.http_status}"
                return result
            # ok / timeout / network_error / too_large / unsupported_type
            result.raw = outcome.raw
            result.http_status = outcome.http_status
            result.content_type = outcome.content_type
            result.charset = outcome.charset
            result.headers = outcome.headers
            result.status = outcome.status
            result.final_url = current
            result.error = outcome.error
            return result
        result.status = "redirect_limit"
        result.final_url = current
        result.error = f"重定向超过 {policy.max_redirects} 次上限"
        return result
    except UrlPolicyError as e:
        result.status = "network_error"
        result.error = str(e)
        return result
    finally:
        result.elapsed_seconds = round(time.monotonic() - started, 4)


def _request_once(parsed: ParsedUrl, ips: list[str], policy: UrlPolicy):
    """对解析集发起一次请求（按序尝试；连接成功即锁定该 IP）。"""
    import http.client as _http
    last_error = ""
    for ip in ips:
        conn = None
        try:
            conn = _pinned_connection(parsed, ip, policy, policy.connect_timeout)
            conn.request("GET", parsed.path, headers={
                "Host": parsed.host_header, "User-Agent": policy.user_agent,
                "Accept": ", ".join(_ACCEPTABLE_TYPES),
                "Connection": "close"})
            response = conn.getresponse()
            status = response.status
            headers = {k.lower(): v for k, v in response.getheaders()}
            location = headers.get("location", "")
            if 300 <= status < 400:
                if not location:
                    return _Outcome("http_error", http_status=status,
                                    error="重定向缺少 Location")
                return _Outcome("redirect", http_status=status, error=location)
            body = _read_limited(response, policy.max_download_bytes)
            if body is None:
                return _Outcome("too_large", http_status=status,
                                error=f"下载超过 {policy.max_download_bytes} 字节上限，已断开")
            content_type = headers.get("content-type", "")
            main_type = content_type.split(";", 1)[0].strip().lower()
            charset = _charset_of(content_type)
            if status >= 400:
                return _Outcome("http_error", http_status=status,
                                error=f"HTTP {status}")
            if main_type and main_type not in _ACCEPTABLE_TYPES:
                return _Outcome("unsupported_type", http_status=status,
                                error=f"内容类型不受支持：{main_type}", content_type=content_type)
            return _Outcome("ok", raw=body, http_status=status,
                            content_type=content_type, charset=charset,
                            headers=headers)
        except socket.timeout as e:
            last_error = f"连接或读取超时（{ip}）"
            continue
        except TimeoutError as e:
            last_error = f"连接或读取超时（{ip}）"
            continue
        except (OSError, ssl.SSLError, _http.HTTPException) as e:
            last_error = f"{type(e).__name__}: {str(e)[:200]}"
            continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
    return _Outcome("network_error", error=last_error or "无法连接")


def _read_limited(response, limit: int) -> bytes | None:
    chunks = []
    total = 0
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _charset_of(content_type: str) -> str:
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset":
            return value.strip().strip("\"'")
    return ""


class _Outcome:
    def __init__(self, status: str, *, raw: bytes | None = None,
                 http_status: int | None = None, content_type: str = "",
                 charset: str = "", headers: dict | None = None,
                 error: str = ""):
        self.status = status
        self.raw = raw
        self.http_status = http_status
        self.content_type = content_type
        self.charset = charset
        self.headers = headers
        self.error = error
