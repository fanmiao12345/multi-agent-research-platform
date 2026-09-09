# -*- coding: utf-8 -*-
"""
harness/context/compressors.py —— Context Compression（DEV_PLAN E4 / 步骤 58-61）

- trim_messages：消息修剪（保留 system + 最近 N 条）
- summarize_head：给"被剪掉"的老消息做滚动摘要（可选 LLM，Mock 用规则版）
- summarize_artifact / summarize_text：工件/长文本的轻量摘要
（工具结果压缩已由 tools/result_processor 承担：此处引用其语义）

实现要点：压缩必须可解释（摘要与原文本同时可见，绝不静默丢信息）。
"""

from __future__ import annotations

from functools import partial
from src.harness.model_gateway import model_call, BudgetStop

call_model = partial(model_call, purpose="summary", role="summary")


def trim_messages(messages: list[dict], keep_last: int = 8) -> list[dict]:
    """修剪：保留开头的 system 消息 + 最近的 keep_last 条。"""
    if len(messages) <= keep_last + 1:
        return list(messages)
    head = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    return head + rest[-keep_last:]


def summarize_head(messages: list[dict], dropped: list[dict], llm=None,
                   max_head_chars: int = 600) -> str:
    """把被 drop 的老消息压缩成一段摘要。llm=None → 规则版抽取。"""
    if llm is not None:
        prompt = ("请把下面的历史对话压缩成一段中文摘要（保留：主题、关键决定、"
                  "未完成事项、用户偏好），不要超过 300 字。\n\n" +
                  "\n".join(f"{m.get('role')}: {str(m.get('content'))[:200]}"
                            for m in dropped[:20]))
        try:
            reply = call_model(llm, [{"role": "user", "content": prompt}])
            return (reply.content or "").strip()
        except BudgetStop:
            raise
        except Exception:  # noqa: BLE001 —— 压缩失败走规则版
            pass
    text = "；".join(str(m.get("content"))[:80] for m in dropped if m.get("content"))
    return f"[历史摘要] {text[:max_head_chars]}"


def summarize_text(text: str, max_chars: int = 800) -> str:
    """规则版文本摘要：保留首段 + 关键句（含数字/问号/结论词的句子）。"""
    paragraphs = [p.strip() for p in (text or "").splitlines() if p.strip()]
    if not paragraphs:
        return ""
    head = paragraphs[0]
    key = []
    for p in paragraphs[1:]:
        if any(ch.isdigit() for ch in p) or "?" in p or "？" in p or p.startswith(("结论", "综上")):
            key.append(p)
        if len("\n".join(key)) > max_chars:
            break
    return (head + ("\n" + "\n".join(key) if key else ""))[:max_chars]
