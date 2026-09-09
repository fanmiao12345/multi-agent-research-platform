# -*- coding: utf-8 -*-
"""
harness/control/loop_guard.py —— Loop Guard（DEV_PLAN H6 / 步骤 89）

在既有 max_iterations 之上增加模式检测（输入：事件序列/状态快照，纯函数可测）：
- 相同 (工具, 参数) 反复调用
- 无 State 进展的循环（消息数量不增长但一直在跑）
- Agent A ↔ B 互转 Handoff 循环
- Reviewer 无限 REWORK
达到阈值 → 报告 stop 与原因；上层据此 Retry / Replan / Interrupt / Stop。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LoopGuardConfig:
    max_iterations: int = 8
    max_same_tool_calls: int = 3
    max_no_progress_rounds: int = 4
    max_handoff_cycles: int = 2
    max_rework: int = 2


def _norm(args) -> tuple:
    if isinstance(args, dict):
        return tuple(sorted((k, str(v)) for k, v in args.items()))
    return (str(args),)


def analyze(events: list[dict], iteration: int = 0,
            config: LoopGuardConfig | None = None) -> dict:
    """events 支持形状：{'type':'tool_call',name,arguments} / {'type':'verdict',value}
    / {'type':'handoff',from,to} / {'type':'message_added'}。返回判定字典。"""
    config = config or LoopGuardConfig()
    flags: dict[str, bool] = {
        "same_tool_loop": False, "no_progress": False,
        "handoff_cycle": False, "rework_loop": False,
    }

    # 1) 相同工具+参数峰值
    calls = [(e.get("name"), _norm(e.get("arguments")))
             for e in events if e.get("type") == "tool_call"]
    peak = max([calls.count(c) for c in set(calls)] or [0])
    if peak >= config.max_same_tool_calls:
        flags["same_tool_loop"] = True

    # 2) 无进展：末尾连续 N 个事件里没有 message_added
    tail = events[-config.max_no_progress_rounds:]
    if tail and len(tail) >= config.max_no_progress_rounds \
            and not any(e.get("type") == "message_added" for e in tail):
        flags["no_progress"] = True

    # 3) Handoff A↔B 往返计数
    handoffs = [(e.get("from"), e.get("to"))
                for e in events if e.get("type") == "handoff"]
    ab = sum(1 for a, b in handoffs if (a, b) == ("A", "B"))
    ba = sum(1 for a, b in handoffs if (a, b) == ("B", "A"))
    if min(ab, ba) >= config.max_handoff_cycles:
        flags["handoff_cycle"] = True

    # 4) REWORK 峰值
    rew = [e for e in events if e.get("type") == "verdict"
           and str(e.get("value", "")).lower() == "rework"]
    if len(rew) >= config.max_rework:
        flags["rework_loop"] = True

    reasons = [k for k, v in flags.items() if v]
    if iteration >= config.max_iterations:
        reasons.append("max_iterations")
    return {"flags": flags, "stop": bool(reasons), "reason": reasons[0] if reasons else "",
            "same_tool_peak": peak, "rework_count": len(rew)}
