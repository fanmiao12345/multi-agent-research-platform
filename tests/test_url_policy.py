# -*- coding: utf-8 -*-
"""测试：URL 策略与 SSRF/私网防护（S2-09 离线部分 / B4-01）。"""
import socket

import pytest

from src.harness.ingest.url_policy import (
    UrlPolicy, UrlPolicyError, is_blocked_address, parse_url, resolve_allowed)


def _parse(url, **kwargs):
    return parse_url(url, policy=UrlPolicy(**kwargs))


@pytest.mark.parametrize("url", [
    "ftp://example.com/a.txt", "file:///etc/passwd", "javascript:alert(1)",
    "gopher://example.com:70/x", "mailto:a@b.c", "http://", "https:///path",
    "http://user:pass@example.com/", "http://u@example.com/", "http://example.com:99999/",
    "http://example.com:0/", "", "   ",
])
def test_parse_url_rejects_bad_input(url):
    with pytest.raises(UrlPolicyError):
        _parse(url)


def test_parse_url_normalizes_host_port_path():
    parsed = _parse("HTTP://Example.COM:443/a/b?q=1#frag")
    assert parsed.scheme == "http" and parsed.hostname == "example.com"
    assert parsed.port == 443 and parsed.path == "/a/b?q=1"
    assert parsed.host_header == "example.com:443"  # http 默认80，显式443必须带端口
    parsed2 = _parse("https://example.com:8443/x")
    assert parsed2.port == 8443 and parsed2.host_header == "example.com:8443"
    parsed3 = _parse("http://example.com:80/")
    assert parsed3.host_header == "example.com"
    assert "userinfo" not in parsed.display and "#frag" not in parsed.display


@pytest.mark.parametrize("addr", [
    "0.0.0.0", "10.1.2.3", "100.64.0.1", "127.0.0.1", "169.254.10.10",
    "172.16.0.1", "172.31.255.255", "192.168.1.1", "192.0.2.9", "198.18.0.1",
    "198.51.100.1", "203.0.113.1", "224.0.0.251", "240.1.2.3", "255.255.255.255",
    "::1", "::ffff:127.0.0.1", "::ffff:192.168.0.1", "fc00::1", "fd12:3456::1",
    "fe80::1", "ff02::1", "64:ff9b::1", "2001:db8::1", "100::1",
])
def test_blocked_addresses(addr):
    assert is_blocked_address(addr)


@pytest.mark.parametrize("addr", [
    "8.8.8.8", "93.184.216.34", "1.1.1.1", "2606:4700:4700::1111",
    "2001:4860:4860::8888",
])
def test_public_addresses_allowed(addr):
    assert not is_blocked_address(addr)


def test_ipv4_mapped_hex_and_canonical_forms():
    assert is_blocked_address("::ffff:7f00:1")  # 127.0.0.1 的十六进制映射
    assert not is_blocked_address("::ffff:808:808")  # 8.8.8.8


def test_resolve_allowed_rejects_any_private_ip(monkeypatch):
    policy = UrlPolicy()
    parsed = _parse("http://internal.example.com/")
    fake = socket.getaddrinfo
    def resolver(host, port, **kwargs):
        if host == "internal.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]
        return fake(host, port, **kwargs)
    with pytest.raises(UrlPolicyError, match="不允许的地址"):
        resolve_allowed(parsed, policy, resolver=resolver)


def test_resolve_allowed_public_and_allowed_hosts(monkeypatch):
    fake = socket.getaddrinfo
    def resolver(host, port, **kwargs):
        if host == "www.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        if host == "loopback.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        if host == "127.0.0.1":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        return fake(host, port, **kwargs)
    public = resolve_allowed(_parse("http://www.example.com/"), UrlPolicy(), resolver=resolver)
    assert public == ["93.184.216.34"]
    # 名字白名单放行回环（测试服务器/可信 intranet 场景）
    allowed = resolve_allowed(_parse("http://loopback.test/"),
                              UrlPolicy(allowed_hosts={"loopback.test"}), resolver=resolver)
    assert allowed == ["127.0.0.1"]
    # IP 原文白名单同样放行
    ip_allowed = resolve_allowed(_parse("http://127.0.0.1:8000/x"),
                                 UrlPolicy(allowed_hosts={"127.0.0.1"}), resolver=resolver)
    assert ip_allowed == ["127.0.0.1"]
    # 未在白名单的私网仍被拒
    with pytest.raises(UrlPolicyError, match="不允许的地址"):
        resolve_allowed(_parse("http://loopback.test/"), UrlPolicy(), resolver=resolver)


def test_resolve_allowed_unresolvable_and_mixed(monkeypatch):
    def no_dns(host, port, **kwargs):
        raise socket.gaierror("no such host")
    with pytest.raises(UrlPolicyError, match="解析失败"):
        resolve_allowed(_parse("http://none.example.com/"), UrlPolicy(), resolver=no_dns)
    def mixed(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
    # 解析结果混入私网 → 整体拒绝（防 rebinding 先手）
    with pytest.raises(UrlPolicyError, match="不允许的地址"):
        resolve_allowed(_parse("http://mixed.example.com/"), UrlPolicy(), resolver=mixed)


def test_redirect_target_is_reparsed_each_hop():
    # 重定向跳点逐跳由 fetcher 重新 parse+resolve；这里验证策略对象本身可复用且只读
    policy = UrlPolicy(allowed_hosts={"127.0.0.1"})
    with pytest.raises(AttributeError):
        policy.max_redirects = 99
    assert parse_url("https://example.com:443/x", policy=policy).scheme == "https"
