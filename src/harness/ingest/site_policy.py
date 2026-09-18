# -*- coding: utf-8 -*-
"""
harness/ingest/site_policy.py —— 站点降权换源策略（O-14 B 方案）

立场：**不伪装 UA、不绕反爬**。抓取器继续自报家门；对已证实/新近以 HTTP 403
拒绝抓取的站点，做的是"尊重 + 换源"——候选搜索结果里直接跳过这些域名，
把名额让给可访问的来源（跳过逐条记录理由，可审计）。

两层名单：
- 静态名单（settings.search_blocked_domains，默认 = 证据基线）：Q2-02 联网批
  107 份来源中 read_failed 38 份全部为 HTTP 403，且集中于商业内容站——
  默认拉黑 baike.baidu.com / wenku.baidu.com / zhihu.com / csdn.net
  （gov.cn 为部分页面拦截，不整域拉黑）。用户可用 SEARCH_BLOCKED_DOMAINS
  环境变量覆盖（逗号分隔；设为空串 = 关闭静态名单）。
- 动态名单（每次任务内）：本次任务里刚被 403 的域名记入，后续补搜不再撞墙。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

# 证据基线：Q2-02 联网批实测全线 403 的商业内容站（见 OPTIMIZATION_BACKLOG O-14）
DEFAULT_BLOCKED_DOMAINS = ("baike.baidu.com", "wenku.baidu.com",
                           "zhihu.com", "csdn.net")

BLOCK_REASON = "站点已知/近期以 403 拒绝抓取，降权换源（O-14 B 方案）"


def domain_of(url: str) -> str:
    """取 URL 的主机名（小写、去端口、去 www 前缀）；解析失败返回空串。"""
    try:
        host = (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def domain_matches(url: str, domain: str) -> bool:
    """URL 是否属于 domain 或其子域（zhihu.com 匹配 zhuanlan.zhihu.com）。"""
    host = domain_of(url)
    target = (domain or "").lower().removeprefix("www.")
    return bool(host) and (host == target or host.endswith("." + target))


def parse_blocked_domains(raw: str) -> tuple[str, ...]:
    """解析 SEARCH_BLOCKED_DOMAINS 环境变量（逗号分隔）；"none" 显式关闭。"""
    items = tuple(x.strip().lower() for x in (raw or "").split(",")
                  if x.strip())
    if items == ("none",):
        return ()
    return items


@dataclass
class DomainBlocklist:
    """静态（配置）+ 动态（任务内 403 实录）两级域名名单。"""

    static: tuple[str, ...] = DEFAULT_BLOCKED_DOMAINS
    dynamic: set[str] = field(default_factory=set)

    def record_rejection(self, url: str, status_code: int | str | None = None) -> bool:
        """任务内实录：403（或显式带 403 的失败说明）才记，其余失败不记。"""
        text = str(status_code or "")
        if "403" not in text:
            return False
        domain = domain_of(url)
        if not domain:
            return False
        self.dynamic.add(domain)
        return True

    def blocks(self, url: str) -> bool:
        return any(domain_matches(url, d) for d in self.all_domains())

    def all_domains(self) -> tuple[str, ...]:
        return tuple(self.static) + tuple(sorted(self.dynamic))

    def domains_for_filter(self) -> frozenset[str]:
        return frozenset(self.all_domains())


def build_domain_blocklist(settings) -> DomainBlocklist:
    """按项目配置构造名单（settings.search_blocked_domains，缺省用证据基线）。"""
    configured = getattr(settings, "search_blocked_domains", None)
    static = tuple(configured) if configured else DEFAULT_BLOCKED_DOMAINS
    return DomainBlocklist(static=static)
