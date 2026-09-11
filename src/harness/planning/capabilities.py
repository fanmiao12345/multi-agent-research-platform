# -*- coding: utf-8 -*-
"""D5-02：能力目录与按可用条件过滤的选型。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    implemented: bool
    order: int
    requires_network: bool = False
    requires_tools: tuple[str, ...] = ()
    min_cost_usd: float = 0.0


@dataclass
class CapabilityDecision:
    available: list[str]
    rejected: list[dict]
    reason: str


class CapabilityCatalog:
    def __init__(self, capabilities: tuple[Capability, ...]):
        self._items = {item.name: item for item in capabilities}

    def all(self) -> list[Capability]:
        return sorted(self._items.values(), key=lambda item: item.order)

    def available(self, *, has_sources: bool, network_available: bool,
                  available_tools: set[str] | frozenset[str],
                  model_available: bool, max_cost_usd: float) -> CapabilityDecision:
        names: list[str] = []
        rejected: list[dict] = []
        for item in self.all():
            reason = ""
            if not item.implemented:
                reason = "尚未实现"
            elif not model_available:
                reason = "模型不可用"
            elif item.requires_network and not network_available:
                reason = "网络/搜索不可用"

            elif item.requires_tools and not set(item.requires_tools).issubset(available_tools):
                reason = f"缺少工具 {sorted(set(item.requires_tools) - set(available_tools))}"
            elif max_cost_usd < item.min_cost_usd:
                reason = "预算不足"
            if reason:
                rejected.append({"mode": item.name, "reason": reason})
            else:
                names.append(item.name)
        return CapabilityDecision(
            available=names, rejected=rejected,
            reason="按实现状态、资料/网络、工具、模型和预算过滤")


def default_capability_catalog() -> CapabilityCatalog:
    """全量目录；D6 六种模式全部实现，是否可用由运行条件过滤。"""
    return CapabilityCatalog((
        Capability("single", "单智能体工具任务与简单资料处理", True, 5),
        Capability("fixed", "固定研究写作链，单点整理/成稿最稳", True, 10),
        Capability("manager_worker", "统筹者按依赖派工并检查成果", True, 15),
        Capability("fanout", "多个独立子题分别研究后汇总", True, 20),
        Capability("dynamic_team", "根据缺口动态调整团队", True, 50),
        Capability("debate", "多方观点与证据反驳", True, 60),
    ))