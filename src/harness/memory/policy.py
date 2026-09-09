# -*- coding: utf-8 -*-
"""
harness/memory/policy.py —— Memory Write Policy（DEV_PLAN F4 / 步骤 71）

长期记忆不允许“全量自动记录”。只有命中条件才允许写：
    用户明确要求 / 稳定偏好 / 已验证的重要事实 / 可复用经验
任何自动提取的候选必须显式过一遍这里，返回 (允许与否, 原因)。
"""

from __future__ import annotations

WRITE_RULES = {
    "explicit": "用户明确要求记住",
    "stable": "稳定偏好（多次出现、低波动）",
    "verified": "已验证的重要事实（带来源/置信度高）",
    "reusable": "可复用经验（成功策略/操作方法）",
}


def should_write(kind: str, *, explicit: bool = False, stable: bool = False,
                 verified: bool = False, reusable: bool = False) -> tuple[bool, str]:
    reasons = []
    if explicit:
        reasons.append(WRITE_RULES["explicit"])
    if kind in ("semantic",) and stable:
        reasons.append(WRITE_RULES["stable"])
    if verified:
        reasons.append(WRITE_RULES["verified"])
    if kind == "procedural" and reusable:
        reasons.append(WRITE_RULES["reusable"])
    if kind == "episodic":
        # 历史任务只保留“值得借鉴”的：失败/成功经验默认不自动沉淀为长期
        return False, "episodic 需要显式或 reusable 才落长期库"
    if reasons:
        return True, "；".join(reasons)
    return False, "未命中任何写入条件（不允许自动全量记录）"
