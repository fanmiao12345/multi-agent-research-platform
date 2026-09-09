# -*- coding: utf-8 -*-
"""
harness/context/budget.py —— Context Budget（DEV_PLAN E3 / 步骤 57）

token 预算：不要无限拼接，按比例分配各来源（比例只是策略，可动态调）。

默认比例（文档 E3 示例）：
    instructions 10% / current task 10% / messages 20% / evidence 30% /
    memory 10% / tools 10% / reserve 10%
"""

from __future__ import annotations

DEFAULT_WEIGHTS = {
    "instructions": 0.10, "task": 0.10, "messages": 0.20,
    "evidence": 0.30, "memory": 0.10, "tools": 0.10, "reserve": 0.10,
}


def estimate_tokens(text: str) -> int:
    """粗略估算：中文约 1 字/ token、英文约 4 字符/token（够预算分配用）。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + max(1, other // 4)


def allocate(total_budget: int, weights: dict | None = None) -> dict:
    """把 total_budget 按权重切成各来源可用上限（返回 {kind: tokens}）。"""
    weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
    weights = {k: v for k, v in weights.items() if v > 0}
    scale = sum(weights.values()) or 1.0
    out: dict[str, int] = {}
    assigned = 0
    keys = list(weights)
    for i, (k, w) in enumerate(weights.items()):
        if i == len(keys) - 1:
            out[k] = max(0, total_budget - assigned)
        else:
            out[k] = int(total_budget * w / scale)
            assigned += out[k]
    return out


def truncate_to(text: str, limit_tokens: int) -> str:
    """按估算 token 截断（保留开头，超限加标记）。"""
    if estimate_tokens(text) <= limit_tokens:
        return text
    # 反向估算：先按比例裁切再精调到不超限
    ratio = limit_tokens / max(1, estimate_tokens(text))
    cut = max(1, int(len(text) * ratio * 0.95))
    result = text[:cut]
    while estimate_tokens(result) > limit_tokens and cut > 20:
        cut -= 20
        result = text[:cut]
    return result + "\n…（按预算截断）…"
