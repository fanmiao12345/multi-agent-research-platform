# -*- coding: utf-8 -*-
"""
eval/evaluators/context_metrics.py —— Context Eval（DEV_PLAN E7 / 步骤 64）

对比 Full Context vs Context Engine 的结果：
    token_saving_ratio / messages_saved / tail_retained（结尾信息是否保留）
作为"质量不下降 + 成本下降"的第一版读数（可解释性指标后续叠加）。
"""

from __future__ import annotations

from src.harness.context.budget import estimate_tokens


def evaluate(full_messages: list[dict], engine_messages: list[dict]) -> dict:
    full_tok = sum(estimate_tokens(str(m.get("content", ""))) for m in full_messages)
    eng_tok = sum(estimate_tokens(str(m.get("content", ""))) for m in engine_messages)

    tail_retained = True
    if full_messages:
        tail = full_messages[-1].get("content")
        tail_retained = any(tail and str(tail) == str(m.get("content"))
                            for m in engine_messages)

    return {
        "full_tokens": full_tok,
        "engine_tokens": eng_tok,
        "token_saving_ratio": (1 - eng_tok / full_tok) if full_tok else None,
        "full_messages": len(full_messages),
        "engine_messages": len(engine_messages),
        "messages_saved": len(full_messages) - len(engine_messages),
        "tail_retained": tail_retained,
    }
