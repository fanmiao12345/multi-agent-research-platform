# -*- coding: utf-8 -*-
"""Welch's t 检验与 A/B 实验运行器：已知参考值 / 退化情形 / 端到端对照。"""
import math
import random

from eval.experiment import ExperimentRunner, betainc_reg, welch_t_test


def test_betainc_matches_known_t_distribution_values():
    # t=2, df=10 的双侧 p 值 ≈ 0.07339（标准参考值）
    p = betainc_reg(5.0, 0.5, 10.0 / 14.0)
    assert abs(p - 0.07339) < 1e-3
    # 退化区间
    assert betainc_reg(2.0, 2.0, 0.0) == 0.0
    assert betainc_reg(2.0, 2.0, 1.0) == 1.0


def test_welch_known_case_shift_by_one():
    # [1..5] vs [2..6]：t=-1，df=8，双侧 p≈0.3466（教科书参考值）
    result = welch_t_test([1, 2, 3, 4, 5], [2, 3, 4, 5, 6])
    assert math.isclose(result["t"], -1.0, abs_tol=1e-9)
    assert math.isclose(result["df"], 8.0, abs_tol=1e-6)
    assert abs(result["p_value"] - 0.3466) < 1e-3
    assert result["p_value"] >= 0.05                 # 差异不显著


def test_welch_zero_variance_same_means():
    result = welch_t_test([5.0, 5.0, 5.0], [5.0, 5.0])
    assert result["p_value"] == 1.0


def test_welch_zero_variance_different_means():
    result = welch_t_test([5.0, 5.0, 5.0], [3.0, 3.0, 3.0])
    assert result["p_value"] == 0.0                  # 常数组均值不同 → 必然显著


def test_welch_too_few_samples():
    assert "error" in welch_t_test([1.0], [2.0, 3.0])


def test_welch_clear_separation_is_significant():
    rng = random.Random(7)
    a = [10 + rng.uniform(-0.5, 0.5) for _ in range(20)]
    b = [12 + rng.uniform(-0.5, 0.5) for _ in range(20)]
    result = welch_t_test(a, b)
    assert result["p_value"] < 0.001
    assert result["mean_b"] > result["mean_a"]


def test_runner_end_to_end_detects_better_variant():
    runner = ExperimentRunner(
        {"baseline": lambda: 10.0, "tuned": lambda: 10.0 + random.Random(1).random()},
        repeats=12, higher_is_better=True)
    report = runner.run()
    comparison = report["comparisons"]["baseline_vs_tuned"]
    assert comparison["significant"] is True
    assert comparison["better"] == "tuned"           # 更高更好 → tuned 胜
    assert comparison["p_value"] == 0.0              # 零方差常数组退化为均值比较


def test_runner_noisy_variants_statistical_verdict():
    rng_a = random.Random(42)
    rng_b = random.Random(43)

    def variant_a() -> float:
        return 10 + rng_a.uniform(-1.0, 1.0)

    def variant_b() -> float:
        return 12.0 + rng_b.uniform(-1.0, 1.0)

    runner = ExperimentRunner({"a": variant_a, "b": variant_b}, repeats=40)
    report = runner.run()
    comparison = report["comparisons"]["a_vs_b"]
    assert comparison["significant"] is True
    assert comparison["better"] == "b"
    assert comparison["diff"] < 0 and comparison["rel_diff"] < 0  # b 更高 → a−b 为负


def test_runner_requires_two_variants():
    try:
        ExperimentRunner({"only": lambda: 1.0})
        raise AssertionError("应当拒绝单变体")
    except ValueError:
        pass
