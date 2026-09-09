# -*- coding: utf-8 -*-
"""测试：Model Routing + Budget（M9 步骤 97-103）。"""
import json
import os
import shutil
import uuid

import pytest

from src.harness.accounting import AccountingLedger, cost_for
from src.harness.budget_control import BudgetManager, degrade_plan
from src.harness.models.factory import build_adapter
from src.harness.models.profiles import PROFILES, get_profile
from src.harness.models.router import (choose_model, classify_complexity,
                                       needs_tools)
from src.llm.mock import MockLLM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def tmp():
    d = os.path.join(ROOT, "workspaces", "_t_m9_" + uuid.uuid4().hex[:6])
    os.makedirs(d, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---------- 97 Model Profiles ----------
def test_profiles_meta():
    assert set(PROFILES) == {"fast", "balanced", "deep", "cheap"}
    deep = get_profile("deep")
    assert deep.tags == ("reasoning", "no_tools")
    assert get_profile("cheap").cost_in < get_profile("deep").cost_in


# ---------- 98/99 Complexity & Routing ----------
def test_classify_complexity():
    assert classify_complexity("你好") == "simple"
    assert classify_complexity("帮我算一下 2+2") == "medium"
    assert classify_complexity("帮我调研远程办公并分析利弊，然后写一篇报告整理成文") == "complex"


def test_choose_model_rules():
    assert choose_model("simple", "balanced") == "fast"
    assert choose_model("complex", "balanced", needs_tools=True) == "balanced"
    assert choose_model("complex", "balanced", needs_tools=False) == "deep"
    assert choose_model("complex", "high_quality", needs_tools=False) == "deep"
    assert choose_model("anything", "low_budget") == "cheap"
    assert choose_model("simple", "balanced", forced="fast") == "fast"
    with pytest.raises(ValueError):
        choose_model("simple", "balanced", needs_tools=True, forced="deep")  # no_tools
    assert needs_tools("帮我计算 6*7") is True


def test_factory_requires_key_unless_explicit_mock(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "")
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    with pytest.raises(ValueError, match="MODEL_API_KEY"):
        build_adapter("balanced")
    assert isinstance(build_adapter("deep", force_mock=True), MockLLM)


# ---------- 100/101 Accounting ----------
def test_cost_for_known_and_unknown():
    assert cost_for("deepseek-chat", 1_000_000, 0) == pytest.approx(0.27)
    assert cost_for("unknown-model", 1, 1) is None


def test_ledger_multi_dimension(tmp):
    ledger = AccountingLedger(os.path.join(tmp, "ledger.jsonl"))
    ledger.record(run_id="R1", agent="researcher", task_id="T1", model="deepseek-chat",
                  prompt_tokens=1_000_000, completion_tokens=0)
    ledger.record(run_id="R1", agent="writer", task_id="T2", model="deepseek-chat",
                  prompt_tokens=0, completion_tokens=1_000_000)
    s = ledger.summarize()
    assert s["by_dimension"]["model"]["deepseek-chat"]["calls"] == 2
    assert s["by_dimension"]["agent"]["researcher"]["cost"] == pytest.approx(0.27)
    assert s["by_dimension"]["task_id"]["T2"]["cost"] == pytest.approx(1.10)
    assert s["total_cost_usd"] == pytest.approx(1.37)


# ---------- 102 Budget Manager ----------
def test_budget_limits():
    b = BudgetManager(max_llm_calls=3, max_tokens=100, max_cost_usd=0.01)
    b.record_llm(prompt=60, completion=20, cost=0.005)
    assert not b.exhausted
    b.record_llm(prompt=60, completion=20, cost=0.005)
    assert b.over_tokens and b.over_cost and b.exhausted
    assert not b.over_llm


def test_degrade_plan_actions():
    b = BudgetManager(max_cost_usd=0.1)
    for _ in range(79):                 # 逼近 LLM 调用上限（默认 100 的 70%）
        b.record_llm(cost=0.0)
    b.record_llm(cost=0.11)             # 超过成本上限
    actions = degrade_plan(b)
    assert "切换 cheap 模型" in actions
    assert "减少 Worker / 跳过 Debate" in actions
    cheap = BudgetManager()
    assert degrade_plan(cheap) == ["无需降级"]


# ---------- I 验收：三档策略可运行 ----------
def test_three_budget_modes_run_and_report(tmp):
    from eval.benchmark_model import BUDGET_MODES, run_strategy

    rows = [run_strategy("帮我调研 X 并写报告", mode, needs_tools=True)
            for mode in BUDGET_MODES]
    profiles = {r["model_profile"] for r in rows}
    assert profiles == {"cheap", "balanced"}  # complex+needs_tools 不走 deep
    assert all(r["termination_reason"] == "success" for r in rows)
    low = next(r for r in rows if r["mode"] == "low_budget")
    assert low["degrade_actions"] != ["无需降级"] or low["model_profile"] == "cheap"
