# -*- coding: utf-8 -*-
"""测试：Benchmark 数据集 / 评测器 / 运行器（Milestone 2 步骤 31-36）。"""
import json
import os
import shutil
import uuid

import pytest

from eval.benchmark import aggregate, load_dataset, render_markdown, run_benchmark
from eval.evaluators import tool_metrics
from eval.evaluators.trajectory import analyze

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def bench_ws():
    out = os.path.join(ROOT, "workspaces", "_t_eval_" + uuid.uuid4().hex[:8])
    os.makedirs(out, exist_ok=True)
    yield out
    shutil.rmtree(out, ignore_errors=True)


def test_dataset_covers_all_categories():
    ds = load_dataset()
    cats = {t["category"] for t in ds["tasks"]}
    assert set(ds["meta"]["categories"]) == cats
    ids = [t["id"] for t in ds["tasks"]]
    assert len(ids) == len(set(ids)) == 13
    safe = [t for t in ds["tasks"] if t["mock_safe"]]
    assert len(safe) >= 4  # 离线可跑子集


def test_tool_metrics_evaluate():
    events = [
        {"type": "tool_call", "name": "calculator",
         "arguments": {"expression": "27*43"}},
        {"type": "tool_result", "name": "calculator", "result": "27*43 = 1161"},
    ]
    r = tool_metrics.evaluate(events, {"tool": ["calculator"],
                                       "arg_eq": {"calculator": {"expression": "27*43"}}})
    assert r["selection_ok"] and r["argument_ok"]
    bad = tool_metrics.evaluate(events, {"tool": ["current_time"]})
    assert not bad["selection_ok"]
    no_tool = tool_metrics.evaluate([], {"no_tool": True})
    assert no_tool["selection_ok"]


def test_benchmark_mock_safe_subset(bench_ws):
    report = run_benchmark(bench_ws)
    t = report["totals"]
    assert t["executed"] == 5
    assert t["passed"] == 5
    assert t["failed"] == 0
    assert t["skipped"] == 8
    assert t["success_rate"] == 1.0
    # 报告可渲染 Markdown
    md = render_markdown(report, None)
    assert "Benchmark Report" in md and "按类别" in md
    # aggregate 输出可直接落 JSON
    assert json.dumps(report, ensure_ascii=False)


def test_trajectory_flags():
    events = [
        {"type": "llm_call", "node": "agent"},
        {"type": "tool_call", "name": "calculator", "arguments": {"expression": "2 +"}},
        {"type": "tool_result", "name": "calculator",
         "result": "[tool-error] 工具 calculator 执行失败"},
        {"type": "tool_call", "name": "calculator", "arguments": {"expression": "2 +"}},
        {"type": "tool_result", "name": "calculator",
         "result": "[tool-error] 工具 calculator 执行失败"},
    ]
    class O:  # 模拟 RunOutcome
        termination_reason = "max_iterations"
    r = analyze(events, O())
    assert r["duplicate_flag"] and r["loop_flag"]
    assert r["tool_error_count"] == 2 and r["error_recovery"] is False
