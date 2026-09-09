# -*- coding: utf-8 -*-
"""
harness/context/policy.py —— Context Source Policy（DEV_PLAN E2 / 步骤 56）

每类上下文内容声明可见性与使用方式：
    ALWAYS_INCLUDE       系统指令/当前任务/输出 Schema：必须给
    RETRIEVE_IF_RELEVANT 记忆/证据：检索命中才给
    SUMMARY_ONLY         历史观察/工件：只给摘要
    PRIVATE              该 Agent 私有，不进任何 prompt
    NEVER_EXPOSE         任何情况下不得外泄（内部审计字段）
"""

from __future__ import annotations

from dataclasses import dataclass

ALWAYS_INCLUDE = "ALWAYS_INCLUDE"
RETRIEVE_IF_RELEVANT = "RETRIEVE_IF_RELEVANT"
SUMMARY_ONLY = "SUMMARY_ONLY"
PRIVATE = "PRIVATE"
NEVER_EXPOSE = "NEVER_EXPOSE"

ALL = (ALWAYS_INCLUDE, RETRIEVE_IF_RELEVANT, SUMMARY_ONLY, PRIVATE, NEVER_EXPOSE)


@dataclass
class ContextSource:
    """一类上下文来源：带策略与（可选）元数据，不直接进 prompt。"""
    kind: str                # system / role / skill / task / evidence / memory / tools / schema / history
    content: str
    policy: str = ALWAYS_INCLUDE
    meta: dict | None = None


def visible_to_model(source: ContextSource) -> bool:
    """能否进入当前模型调用（PRIVATE / NEVER_EXPOSE 一律不给）。"""
    return source.policy in (ALWAYS_INCLUDE, RETRIEVE_IF_RELEVANT, SUMMARY_ONLY)


def summarize_or_drop(source: ContextSource) -> str:
    """按策略给出该来源进入 prompt 的实际文本。"""
    if source.policy == SUMMARY_ONLY:
        head = source.content.strip().replace("\n", " ")[:150]
        return f"{source.kind}摘要：{head}…" if head else ""
    return source.content
