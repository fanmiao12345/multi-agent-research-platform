# -*- coding: utf-8 -*-
"""
harness/ingest/url_policy.py —— URL 协议与地址边界（S2-09 离线可验收部分）

防护原则（SSRF / DNS rebinding / 回环与私网）：
- 只允许 http/https；拒绝带 userinfo 的 URL；hostname 强制非空；
- 每次解析出的每一个地址都要过白名单：私网/回环/链路本地/组播/保留/文档网段默认拒绝；
- 域名解析结果中出现任何不允许的地址就整体拒绝（混合解析=可疑，不挑着连）；
- 显式受信场景（本机测试服务器、受信 intranet、可信本地模型 endpoint）
  通过 UrlPolicy.allowed_hosts（名字或 IP 原文）放行，不允许逐任务开私网大闸；
- 重定向的每一跳都重新走 parse+resolve（见 fetcher），地址变化即重判。

诚实边界：本模块做地址级防护与策略检查；代理环境、TLS 证书以外的攻击面、
以及"域名解析与连接之间地址变化"的最后一道防线由 fetcher 的"按解析结果直连
+ 连接后校验 peer 地址"承担（本模块只保证解析集合法）。
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# 资源受限/保留地址（S2-09 提到的私网、回环、链路本地等，加常见的保留与文档网段）
_BLOCKED_NETS_V4 = [
    ipaddress.ip_network(n) for n in (
        "0.0.0.0/8",        # 本网络
        "10.0.0.0/8",       # 私网
        "100.64.0.0/10",    # CGNAT
        "127.0.0.0/8",      # 回环
        "169.254.0.0/16",   # 链路本地
        "172.16.0.0/12",    # 私网
        "192.0.0.0/24",     # IETF 协议保留
        "192.0.2.0/24",     # TEST-NET-1
        "192.168.0.0/16",   # 私网
        "198.18.0.0/15",    # 基准测试
        "198.51.100.0/24",  # TEST-NET-2
        "203.0.113.0/24",   # TEST-NET-3
        "224.0.0.0/4",      # 组播
        "240.0.0.0/4",      # 保留
    )]
_BLOCKED_NETS_V6 = [
    ipaddress.ip_network(n) for n in (
        "::/128", "::1/128",          # 未指定/回环
        "100::/64",                   # discard-only
        "64:ff9b::/96",               # NAT64（内含映射目标不可见，保守拦截）
        "2001:db8::/32",              # 文档
        "fc00::/7",                   # 唯一本地
        "fe80::/10",                  # 链路本地
        "ff00::/8",                   # 组播
    )]


class UrlPolicyError(ValueError):
    """URL 或地址不满足策略：明确失败，不回退到其他地址/协议。"""


def _ip_object(value: str | int) -> ipaddress._BaseAddress:
    ip = ipaddress.ip_address(value)
    mapped = getattr(ip, "ipv4_mapped", None)  # ::ffff:1.2.3.4 视同 IPv4
    return mapped if mapped is not None else ip


def is_blocked_address(value: str | int) -> bool:
    """地址是否命中私网/回环/链路本地/保留网段（IPv4-mapped IPv6 按 IPv4 判定）。"""
    ip = _ip_object(value)
    nets = _BLOCKED_NETS_V4 if isinstance(ip, ipaddress.IPv4Address) else _BLOCKED_NETS_V6
    return any(ip in net for net in nets)


@dataclass(frozen=True)
class UrlPolicy:
    """一次抓取任务的地址策略；全部字段只读。"""
    allowed_hosts: frozenset[str] = frozenset()  # 显式放行的名字或 IP 原文（绕过私网拦截，仍限 http/https）
    max_redirects: int = 5
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    max_download_bytes: int = 10 * 1024 * 1024
    user_agent: str = "agent-mvp/0.1 (local research import)"

    def __post_init__(self):
        for name in ("connect_timeout", "read_timeout"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} 必须为正数")
        if not isinstance(self.max_redirects, int) or self.max_redirects < 0:
            raise ValueError("max_redirects 必须为非负整数")
        if not isinstance(self.max_download_bytes, int) or self.max_download_bytes <= 0:
            raise ValueError("max_download_bytes 必须为正整数")


@dataclass(frozen=True)
class ParsedUrl:
    scheme: str
    hostname: str        # ASCII（punycode 已编码）、小写
    port: int            # 已按协议默认值补齐
    path: str            # 含 query 的请求目标
    display: str         # 规范化展示（不含 userinfo/片段）

    @property
    def host_header(self) -> str:
        default = 443 if self.scheme == "https" else 80
        if self.port == default:
            return self.hostname
        return f"{self.hostname}:{self.port}"


def parse_url(url: str, *, policy: UrlPolicy | None = None) -> ParsedUrl:
    """拆解并校验一个 URL；不符合协议/格式边界直接抛 UrlPolicyError。"""
    policy = policy or UrlPolicy()
    if not isinstance(url, str) or not url.strip():
        raise UrlPolicyError("URL 不能为空")
    try:
        parts = urlsplit(url.strip())
    except ValueError as e:
        raise UrlPolicyError(f"URL 无法解析：{e}") from None
    if parts.scheme.lower() not in ("http", "https"):
        raise UrlPolicyError(f"只允许 http/https 协议：{parts.scheme or '(无协议)'}")
    if parts.username is not None or parts.password is not None:
        raise UrlPolicyError("URL 不允许携带 userinfo（用户名/密码）")
    host = (parts.hostname or "").strip().rstrip(".")
    if not host:
        raise UrlPolicyError("URL 缺少主机名")
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise UrlPolicyError("主机名无法按 IDNA 编码") from None
    try:
        port = parts.port
    except ValueError:
        raise UrlPolicyError("端口无效")
    if port is None:
        port = 443 if parts.scheme.lower() == "https" else 80
    if not (1 <= port <= 65535):
        raise UrlPolicyError(f"端口越界：{port}")
    if parts.fragment:
        pass  # 片段不参与请求
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return ParsedUrl(scheme=parts.scheme.lower(), hostname=ascii_host, port=port,
                     path=path, display=f"{parts.scheme}://{host}" + (f":{port}" if port not in (80, 443) else "") + path)


def resolve_allowed(parsed: ParsedUrl, policy: UrlPolicy,
                    resolver=socket.getaddrinfo) -> list[str]:
    """解析主机并对每个地址做白名单检查；含任何不允许的地址则整体拒绝。

    返回可直连的 IP 列表（去重、保序）。resolver 形如
    socket.getaddrinfo(host, port, type=SOCK_STREAM)。
    """
    try:
        entries = resolver(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise UrlPolicyError(f"域名解析失败：{parsed.hostname}（{type(e).__name__}）") from None
    ips: list[str] = []
    for entry in entries:
        sockaddr = entry[4]
        ip = sockaddr[0] if isinstance(sockaddr, tuple) else str(sockaddr)
        ip = ip.split("%", 1)[0]  # 去掉 IPv6 链路本地 zone id
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise UrlPolicyError(f"域名没有可用地址：{parsed.hostname}")
    allowed_names = {str(x).lower() for x in policy.allowed_hosts}
    blocked: list[str] = []
    for ip in ips:
        if ip.lower() in allowed_names or parsed.hostname.lower() in allowed_names:
            continue
        if is_blocked_address(ip):
            blocked.append(ip)
    if blocked:
        raise UrlPolicyError(
            f"解析结果包含不允许的地址（私网/回环/链路本地/保留）：{', '.join(blocked)}")
    return ips
