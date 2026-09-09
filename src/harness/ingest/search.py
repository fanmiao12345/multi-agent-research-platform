# -*- coding: utf-8 -*-
"""
harness/ingest/search.py —— 搜索服务网关占位（S2-03/04）

设计（B4 阶段只做"未配置即禁用"的边界与记账字段，不接任何具体服务商）：
- SEARCH_PROVIDER 未配置（默认）→ 搜索明确禁用：任何入口都得到可操作的
  SearchNotConfigured 提示，绝不静默退回“假装搜过”或改用 Mock 数据；
- 配置后由实施按官方文档核验接口/费用（S2-04）再把具体 provider 接入；
- 每次搜索调用预留 S2-04/S4-10 记账字段（root_job_id、query、ranked结果、
  已知/未知费用、耗时），未知费用显式标记 unknown，不记零。

本模块不发起任何网络请求（未配置 provider）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


class SearchNotConfigured(ValueError):
    """搜索服务未配置：任务应明确告知用户，而不是用假结果继续。"""


SUPPORTED_PROVIDERS: tuple[str, ...] = ()   # B4 未接入任何真实服务商
CONFIG_KEYS = ("SEARCH_PROVIDER", "SEARCH_API_KEY", "SEARCH_BASE_URL",
               "SEARCH_MAX_RESULTS")


def search_enabled(search_provider: str = "") -> bool:
    return bool((search_provider or "").strip())


def check_configured(provider: str, settings) -> None:
    """调用前检查；provider 空或不是已实现的服务商一律明确失败。"""
    provider = (provider or "").strip().lower()
    if not provider:
        raise SearchNotConfigured(
            "搜索服务未配置：请在 .env 设置 SEARCH_PROVIDER / SEARCH_API_KEY；"
            "未配置时本任务不会搜索，仅使用用户提供的资料与链接")
    if provider not in SUPPORTED_PROVIDERS:
        raise SearchNotConfigured(
            f"搜索服务 {provider} 尚未接入：B4 阶段先由实施按官方接口与费用核验后接入（S2-03/04）")


@dataclass
class SearchRecord:
    """一次搜索调用的账本记录骨架（S2-04：付费搜索计入根账本）。"""
    query: str
    provider: str
    root_job_id: str = ""
    max_results: int = 5
    started_at: str = ""
    elapsed_seconds: float = 0.0
    estimated_cost_usd: float | None = None   # 无法估算时保持 None=未知，不记零
    cost_known: bool = False
    urls: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "query", "provider", "root_job_id", "max_results", "started_at",
            "elapsed_seconds", "estimated_cost_usd", "cost_known", "urls", "error")}
