# -*- coding: utf-8 -*-
"""
harness/context/builder.py —— Context Builder（DEV_PLAN E1 / 步骤 55）

每一次模型调用前统一经过这里组装上下文：
    候选内容：System / Role / Skill / Task / Evidence / Memory / Messages / Tools / Output Schema
    每类内容 = ContextSource（带 policy）；ALWAYS 直给、SUMMARY_ONLY 摘要化、
    PRIVATE/NEVER_EXPOSE 跳过；整包按 Context Budget 分配 token 上限。

返回 messages 与一份 stats（每来源实耗 token），保证"每次调用都有可解释的
Context 构建过程"（Milestone 5 验收）。
"""

from __future__ import annotations

from src.harness.context import budget as budget_mod
from src.harness.context.compressors import trim_messages
from src.harness.context.policy import (SUMMARY_ONLY, ContextSource,
                                        visible_to_model)


def compose_context(question: str, sources: list[ContextSource], *,
                    history: list[dict] | None = None,
                    total_budget: int = 6000,
                    base_system: str = "") -> tuple[list[dict], dict]:
    """组装最终 messages（OpenAI 风格）并返回每来源统计。

    sources 的 kind 建议取 budget 表里的键（instructions/task/evidence/memory/tools）。
    """
    limits = budget_mod.allocate(total_budget, weights={
        k: v for k, v in budget_mod.DEFAULT_WEIGHTS.items()
        if k not in ("reserve", "messages")})
    # reserve 加回给 messages
    messages_lim = total_budget - sum(limits.values())

    system_parts: list[str] = []
    if base_system:
        system_parts.append(base_system)
    stats: dict[str, int] = {}
    task_suffix = ""
    for src in sources:
        if not visible_to_model(src):
            stats[f"{src.kind}:(跳过 {src.policy})"] = 0
            continue
        text = src.content
        lim = limits.get(src.kind)
        if lim is not None:
            text = budget_mod.truncate_to(text, lim)
        if src.kind == "system" or src.kind == "instructions":
            system_parts.append(text)
        else:
            task_suffix += f"\n[{src.kind}]\n{text}\n"
        stats[src.kind] = budget_mod.estimate_tokens(text)

    history = history or []
    if history:
        history = trim_messages(history, keep_last=max(2, int(messages_lim / 120)))

    messages = []
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    messages.extend(history)
    messages.append({"role": "user",
                     "content": question + (task_suffix or "")})
    stats["total_estimated"] = sum(stats.values()) + \
        budget_mod.estimate_tokens(messages[-1]["content"]) + \
        sum(budget_mod.estimate_tokens(str(m.get("content"))) for m in history)
    return messages, stats
