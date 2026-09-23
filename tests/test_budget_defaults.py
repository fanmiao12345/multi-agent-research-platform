# -*- coding: utf-8 -*-
"""Q4-D1 预算默认值：研究写作链的真实结构（逐来源证据 + 写作审校修订）需要
40 次调用 / 65536 输出 token 量级的兜底；费用/时间护栏不变（2026-09-23 拍板）。"""
from src.application.request import TaskRequest


def test_default_budgets_fit_research_chain():
    request = TaskRequest(task="综述初稿")
    assert request.max_calls == 40
    assert request.max_output_tokens == 65536
    assert request.max_cost == 0.15      # 钱护栏不变：失控任务仍会被 cost_limit 截停
    assert request.max_seconds == 600    # 时间护栏不变
