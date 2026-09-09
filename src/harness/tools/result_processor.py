# -*- coding: utf-8 -*-
"""
harness/tools/result_processor.py —— Tool Result Processing（步骤 48）

对工具输出做进入上下文前的处理：超长截断、连续重复行去重、文本化。
Executable 统一经 process() 后再写回 role=tool 消息，防止大输出撑爆上下文。

截断策略：超出 max_chars 时按 3:1 分给头尾，中间留截断标记，保证总量 ≈ max_chars。
"""

from __future__ import annotations


def process(name: str, raw: object, max_chars: int = 4000,
            head_keep: int | None = None, tail_keep: int | None = None) -> str:
    text = str(raw)
    lines = text.splitlines()
    deduped: list[str] = []
    for line in lines:
        if deduped and line == deduped[-1]:
            continue                    # 连续重复行只留一行
        deduped.append(line)
    text = "\n".join(deduped)

    if len(text) > max_chars:
        marker = f"\n…（结果过长已截断：原 {len(text)} 字符）…\n"
        keep = max_chars - len(marker)
        if keep < 40:  # 极端小阈值兜底
            return text[:max_chars] + marker
        if head_keep is None or head_keep + (tail_keep or 0) + len(marker) > max_chars:
            head_keep = int(keep * 3 / 4)
            tail_keep = keep - head_keep
        head = text[:head_keep]
        tail = text[-tail_keep:]
        text = f"{head}{marker}{tail}"
    return text
