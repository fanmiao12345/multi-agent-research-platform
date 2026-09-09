# -*- coding: utf-8 -*-
"""
harness/structured.py —— Structured Output 约定（DEV_PLAN A3 / 步骤 22）

重要内部节点（Planner / Tool Decision / Handoff / Review / Memory Write / Eval）
尽量输出结构化结果，避免 Agent 之间靠自然语言猜字段。

两种受支持的格式（都可容忍失败并返回 None，由调用方兜底）：
1. JSON 块：```json { ... } ```（适合字段多、嵌套）
2. 标签行：KEY: value（适合字段少、要求严格的单行决策）

统一约定：结构化输出失败 = 返回 None + 记录原文，绝不让解析器抛异常砸掉流程。
"""

from __future__ import annotations

import json
import re
from typing import Any


def extract_json(text: str) -> dict | None:
    """从回复里提取第一个 JSON 对象（容忍 ```json 围栏与前后杂文）。"""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    if not candidates:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            candidates.append(text[start:end + 1])
    for raw in candidates:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def parse_labels(text: str, keys: tuple[str, ...]) -> dict[str, str] | None:
    """解析严格的 "KEY: value" 行（keys 之外的键忽略）。

    任意必需键缺失视为解析失败（返回 None），由调用方决定兜底策略。
    """
    found: dict[str, str] = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:：]\s*(.*?)\s*$", line)
        if m and m.group(1).lower() in {k.lower() for k in keys}:
            found[m.group(1).lower()] = m.group(2)
    if not all(k.lower() in found for k in keys):
        return None
    return found


def decision_text(key: str, value: Any) -> str:
    """把单值决策编码成标签行（供模型直接回复的模板）。"""
    return f"{key}: {value}"
