# -*- coding: utf-8 -*-
"""
orchestration/pipeline.py —— Pipeline 策略（步骤 76）

固定顺序接力：Research → Organize → Write → Review。
每棒产出通过 Handoff Pack 卡头传给下一棒（State Passing + Stage Contract）。
"""

from __future__ import annotations

from src.orchestration.base import (StrategyResult, Worker, pack_card)

DEFAULT_CHAIN = ("researcher", "organizer", "writer", "reviewer")


def run_pipeline(task: str, worker: Worker, chain=DEFAULT_CHAIN,
                 name: str = "pipeline") -> StrategyResult:
    prev = None
    stages = []
    calls = 0
    for idx, role in enumerate(chain):
        task_text = pack_card(task, f"第{idx + 1}棒·{role}", prev) + task
        if role == "writer":  # 成稿棒不再重复整段目标，避免上下文膨胀
            task_text = f"基于上一棒素材包成稿（原任务：{task[:100]}）\n"
        out = worker(task_text, role)
        calls += 1
        prev = out
        stages.append({"role": role, "output": out[:120]})
    return StrategyResult(name=name, final=prev or "", worker_calls=calls,
                          stages=stages)
