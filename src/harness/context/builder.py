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
                                        summarize_or_drop, visible_to_model)


def compose_context(question: str, sources: list[ContextSource], *,
                    history: list[dict] | None = None,
                    total_budget: int = 6000,
                    base_system: str = "",
                    append_question: bool = True,
                    user_message_kinds: tuple[str, ...] = (),
                    reserve_message_window: bool = False) -> tuple[list[dict], dict]:
    """组装最终 messages（OpenAI 风格）并返回每来源统计。

    sources 的 kind 建议取 budget 表里的键（instructions/task/evidence/memory/tools）。
    user_message_kinds：列在其中的 kind 不再单独发 system 消息，而是并入
    本轮 User Message（<<CONTEXT>> 标记保留）——记忆/证据类内容每次检索结果
    都不同，放进 User Message 可让 system 前缀逐字节稳定（Prompt Cache 友好）；
    默认空元组 = 旧行为（单独 system 消息）。
    reserve_message_window（O-15 就绪开关，默认 False = 旧行为）：True 时
    messages/reserve 份额真正保留给对话历史（不再被 allocate 归一化挤占），
    历史窗口从"恒为最近 2 条"恢复到与预算成比例的正常量级。修复会改变所有
    链路提示词长度，须在真实批次间隙切换（见 OPTIMIZATION_BACKLOG O-15）。
    """
    if reserve_message_window:
        # 全权重分配（和恰为 1.0，无归一化放大），messages+reserve 真正留给历史
        limits = budget_mod.allocate(total_budget,
                                     weights=dict(budget_mod.DEFAULT_WEIGHTS))
        messages_lim = limits.pop("messages") + limits.pop("reserve")
    else:
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
    user_suffix = ""
    for src in sources:
        if not visible_to_model(src):
            stats[f"{src.kind}:(跳过 {src.policy})"] = 0
            continue
        text = summarize_or_drop(src) if src.policy == SUMMARY_ONLY else src.content
        lim = limits.get(src.kind)
        if lim is not None:
            original = text
            text = budget_mod.truncate_to(text, lim)
            if text != original:
                stats[f"{src.kind}:(截断)"] = 1
        if src.kind == "system" or src.kind == "instructions":
            system_parts.append(text)
        elif src.kind in user_message_kinds:
            user_suffix += f"\n[{src.kind}]\n{text}\n"
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
    if append_question:
        if task_suffix:
            messages.append({"role": "system",
                             "content": "<<CONTEXT>>\n" + task_suffix.strip()})
        user_content = question
        if user_suffix:
            user_content = ("<<CONTEXT>>\n" + user_suffix.strip() +
                            "\n\n[本轮问题]\n" + question)
        messages.append({"role": "user", "content": user_content})
    stats["total_estimated"] = sum(stats.values()) + \
        (budget_mod.estimate_tokens(messages[-1]["content"])
         if append_question and messages else 0) + \
        sum(budget_mod.estimate_tokens(str(m.get("content"))) for m in history)
    return messages, stats
