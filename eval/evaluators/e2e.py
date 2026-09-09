# -*- coding: utf-8 -*-
"""
eval/evaluators/e2e.py —— End-to-End Eval（步骤 35）

success 判定 = 工具期望满足（selection+argument）∧ 文本期望满足 ∧ 终止符合期望。
文本期望：final_contains（子串）或 final_regex（正则）；终止期望：termination_reason。
"""

from __future__ import annotations

import re


def evaluate(expect: dict, final_text: str, tool: dict, outcome) -> dict:
    checks: list[tuple[str, bool]] = []

    ok_tool = True
    if "tool" in expect or "no_tool" in expect:
        ok_tool = tool["selection_ok"] and tool["argument_ok"]
        checks.append(("tool_usage", ok_tool))

    text_ok = True
    text_detail = ""
    for needle in expect.get("final_contains") or []:
        if needle not in (final_text or ""):
            text_ok = False
            text_detail = f"缺子串「{needle}」"
            break
    if text_ok and expect.get("final_regex"):
        if not re.search(expect["final_regex"], final_text or ""):
            text_ok = False
            text_detail = f"正则不匹配 {expect['final_regex']}"
    if expect.get("final_contains") or expect.get("final_regex"):
        checks.append(("final_text", text_ok))

    reason_ok = True
    if expect.get("termination_reason"):
        reason_ok = getattr(outcome, "termination_reason", "") == expect["termination_reason"]
        checks.append(("termination", reason_ok))

    success = all(ok for _, ok in checks)
    return {"success": success, "checks": dict(checks),
            "text_detail": text_detail, "final_len": len(final_text or "")}
