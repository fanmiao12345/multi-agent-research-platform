# -*- coding: utf-8 -*-
"""
eval/evaluators/trajectory.py —— Trajectory Eval（步骤 34）

根据 trace 判断执行过程质量（B6 的部分落地）：
- duplicate_calls：同一 (工具, 参数) 被重复调用 >= 2 次（Mock 故障任务的预期行为）
- loop_flag：迭代到上限被 Loop Guard 掐断（termination_reason == max_iterations）
- tool_errors：出现过 [tool-error] 前缀的工具结果
- error_recovery：出现过工具错误但最终仍正常收尾（reason == success）
"""

from __future__ import annotations


def analyze(events: list[dict], outcome) -> dict:
    tool_results = [e for e in events if e.get("type") == "tool_result"]
    errors = [r["result"] for r in tool_results
              if isinstance(r.get("result"), str) and r["result"].startswith("[tool-error]")]

    calls = [(e.get("name"), _norm_args(e.get("arguments")))
             for e in events if e.get("type") == "tool_call"]
    dup = max([calls.count(c) for c in set(calls)] or [0])

    reason = getattr(outcome, "termination_reason", "")
    return {
        "llm_calls": sum(1 for e in events if e.get("type") == "llm_call"),
        "duplicate_call_peak": dup,
        "duplicate_flag": dup >= 2,
        "loop_flag": reason == "max_iterations",
        "tool_error_count": len(errors),
        "error_recovery": bool(errors) and reason == "success",
    }


def _norm_args(args) -> tuple:
    if not isinstance(args, dict):
        return (str(args),)
    return tuple(sorted((k, str(v)) for k, v in args.items()))
