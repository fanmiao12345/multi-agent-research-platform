# -*- coding: utf-8 -*-
"""
harness/models/router.py —— Complexity Router + Dynamic Model Routing（步骤 98-99）

两层：
1) classify_complexity(task, needs_tools)：确定性启发式（可换 LLM 版，接口不变）
   → simple / medium / complex
2) choose_model(complexity, budget_mode, needs_tools, forced=None)
   → 返回档案名；原则：工具任务不用 no_tools 档案；low_budget 一律 cheap。
"""

from __future__ import annotations

import re

_MULTI_MARKERS = ("调研", "查资料", "写一篇", "报告", "分析", "对比", "整理", "计划", "多步骤")
_TOOL_HINTS = ("计算", "算一下", "几点", "搜索", "查一下")


def classify_complexity(task: str, needs_tools: bool = False) -> str:
    text = task or ""
    hits = sum(1 for k in _MULTI_MARKERS if k in text)
    length = len(text)
    wants_tool = needs_tools or any(k in text for k in _TOOL_HINTS)
    if hits >= 2 or length > 300:
        return "complex"
    if hits == 1 or wants_tool or length > 60:
        return "medium"
    return "simple"


def _supports_tools(profile_name: str) -> bool:
    from src.harness.models.profiles import get_profile
    return "no_tools" not in get_profile(profile_name).tags


def choose_model(complexity: str, budget_mode: str = "balanced",
                 needs_tools: bool = False, forced: str | None = None) -> str:
    if forced:
        if needs_tools and not _supports_tools(forced):
            raise ValueError(f"档案 {forced} 不支持工具调用（no_tools），请换用 fast/balanced/cheap")
        return forced
    if budget_mode == "low_budget":
        return "cheap"
    if budget_mode == "high_quality":
        # 复杂推理任务才上 deep；需要工具时 deep(no_tools) 不可用 → balanced
        return "deep" if (not needs_tools and complexity == "complex") else "balanced"
    # balanced 默认档
    if complexity == "complex":
        return "deep" if not needs_tools else "balanced"
    if complexity == "medium":
        return "balanced"
    return "fast"


def needs_tools(task: str) -> bool:
    return any(k in task for k in _TOOL_HINTS) or "计算" in task or "时间" in task
