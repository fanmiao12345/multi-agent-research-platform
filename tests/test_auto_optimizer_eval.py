# -*- coding: utf-8 -*-
"""护栏版参数反馈闭环：规则触发 / 硬边界 / 白名单 / dry_run / 审计日志。"""
import json

from eval.auto_optimizer import (MAX_CHANGES_PER_CYCLE, PARAM_BOUNDS,
                                 AdjustmentRule, AutoOptimizer)


def test_rule_fires_on_high_truncation_ratio():
    optimizer = AutoOptimizer()
    metrics = {"context": {"truncated_ratio": 0.5}}
    proposals = optimizer.analyze(metrics, params={"context_budget": 4000})
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["param"] == "context_budget"
    assert proposal["proposed"] == 4600              # 4000 × 1.15


def test_no_trigger_within_threshold():
    optimizer = AutoOptimizer()
    metrics = {"context": {"truncated_ratio": 0.1}}
    assert optimizer.analyze(metrics, params={"context_budget": 4000}) == []


def test_proposal_clamped_to_hard_bounds():
    optimizer = AutoOptimizer()
    metrics = {"context": {"truncated_ratio": 0.9}}
    proposals = optimizer.analyze(metrics, params={"context_budget": 19000})
    assert proposals[0]["proposed"] == 20000         # 19000×1.15=21850 → 夹到上限


def test_no_proposal_when_already_at_bound():
    optimizer = AutoOptimizer()
    metrics = {"context": {"truncated_ratio": 0.9}}
    assert optimizer.analyze(metrics, params={"context_budget": 20000}) == []


def test_cost_cap_never_in_whitelist():
    assert "max_cost" not in PARAM_BOUNDS
    # 即便写了指向 max_cost 的规则也会被白名单拦下
    optimizer = AutoOptimizer(rules=(
        AdjustmentRule(rule_id="greedy", metric="latency.latency_p95_s",
                       above=1.0, param="max_cost", factor=2.0, reason="x"),))
    assert optimizer.analyze({"latency": {"latency_p95_s": 500}}) == []


def test_missing_metric_is_skipped():
    optimizer = AutoOptimizer()
    assert optimizer.analyze({"unrelated": {"x": 1}}) == []


def test_dry_run_returns_patch_without_mutating():
    optimizer = AutoOptimizer()
    metrics = {"context": {"truncated_ratio": 0.5}}
    proposals = optimizer.analyze(metrics, params={"context_budget": 4000})
    params = {"context_budget": 4000}
    outcome = optimizer.apply(proposals, params, dry_run=True)
    assert outcome["applied"] is False and outcome["dry_run"] is True
    assert outcome["patch"] == {"context_budget": 4600}
    assert params == {"context_budget": 4000}        # 原参数不被改动


def test_apply_updates_params_and_writes_audit_log(tmp_path):
    import math

    optimizer = AutoOptimizer(log_dir=tmp_path / "logs")
    metrics = {"latency": {"latency_p95_s": 500.0}}
    proposals = optimizer.analyze(metrics, params={"max_output_tokens": 8192})
    params = {"max_output_tokens": 8192}
    outcome = optimizer.apply(proposals, params, dry_run=False)
    assert outcome["applied"] is True
    assert math.isclose(outcome["new_params"]["max_output_tokens"],
                        8192 * 0.9, rel_tol=1e-9)
    audit = list((tmp_path / "logs").glob("optimizer_*.json"))
    assert len(audit) == 1
    data = json.loads(audit[0].read_text(encoding="utf-8"))
    assert "max_output_tokens" in data["changes"]
    assert data["basis"][0]["rule_id"] == "lower_tokens_on_slow_p95"


def test_cycle_cap_limits_changes():
    rules = tuple(
        AdjustmentRule(rule_id=f"r{i}", metric=f"m{i}", above=0.0,
                       param="max_calls", factor=1.0 + 0.01 * (i + 1),
                       reason="x")
        for i in range(5))
    optimizer = AutoOptimizer(rules=rules)
    metrics = {f"m{i}": 1.0 for i in range(5)}
    proposals = optimizer.analyze(metrics, params={"max_calls": 100})
    assert len(proposals) == MAX_CHANGES_PER_CYCLE   # 每轮最多 3 项
    outcome = optimizer.apply(proposals, {"max_calls": 100}, dry_run=True)
    # 同一参数每轮只调一次：3 条同参数建议 → 首条生效（101.0），其余计入 skipped
    assert outcome["patch"] == {"max_calls": 101.0}
    assert outcome["skipped"] == 2
