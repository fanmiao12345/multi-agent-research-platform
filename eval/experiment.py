# -*- coding: utf-8 -*-
"""
eval/experiment.py —— A/B 对照实验 + Welch's t 检验（ExperimentRunner）

对比两组变体（如两种模型档位 / 两种提示词 / fixed vs fanout）在同一批任务上的
量化指标：控制变量由调用方保证（同数据集、同预算、同评测口径），这里负责
    1. 按 repeats 次重复运行两组变体，采集每次的指标值（如质量分/耗时/费用）；
    2. Welch's t 检验判断均值差异是否统计显著——两组方差不必相等、样本数
       不必相同，是 A/B 实验的标准检验。

零依赖实现：t 分布双侧 p 值用恒等式 p = I_x(df/2, 1/2)（x = df/(df+t²)），
正则化不完全 Beta 用连分式算法（Numerical Recipes 标准做法），不引 scipy。

用法：
    from eval.experiment import ExperimentRunner
    runner = ExperimentRunner({"fast": run_fast_variant, "deep": run_deep_variant},
                              repeats=20)
    report = runner.run()
    print(report["comparisons"]["fast_vs_deep"]["p_value"])
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable


# ---- 正则化不完全 Beta（p 值计算的数学底座）----

def _betacf(a: float, b: float, x: float, *, max_iter: int = 300,
            eps: float = 3e-12) -> float:
    """正则化不完全 Beta 的连分式（Lentz 加速，NR 标准实现）。"""
    tiny = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc_reg(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta 函数 I_x(a, b)，值域 [0, 1]。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_front = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                + a * math.log(x) + b * math.log(1.0 - x))
    front = math.exp(ln_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def welch_t_test(a: list[float], b: list[float]) -> dict:
    """Welch's t 检验（双侧）。返回 t / 自由度 / p 值 / 两组均值与方差。

    a、b 是两次变体各自的指标样本（如 20 次运行的质量分）。
    """
    n_a, n_b = len(a), len(b)
    if n_a < 2 or n_b < 2:
        return {"error": f"每组至少 2 个样本（收到 {n_a}/{n_b}）"}
    mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
    var_a = statistics.variance(a)
    var_b = statistics.variance(b)
    se2 = var_a / n_a + var_b / n_b
    if se2 <= 0:
        # 两组完全无方差：均值相同则无差异，不同则样本信息不足
        same = math.isclose(mean_a, mean_b, rel_tol=1e-12, abs_tol=1e-12)
        return {"t": 0.0, "df": float(n_a + n_b - 2), "p_value": 1.0 if same else 0.0,
                "mean_a": mean_a, "mean_b": mean_b,
                "var_a": var_a, "var_b": var_b, "n_a": n_a, "n_b": n_b,
                "note": "样本无方差，退化为均值比较"}
    t_stat = (mean_a - mean_b) / math.sqrt(se2)
    denom = ((var_a ** 2) / (n_a ** 2 * (n_a - 1))
             + (var_b ** 2) / (n_b ** 2 * (n_b - 1)))
    df = se2 ** 2 / denom if denom > 0 else float(n_a + n_b - 2)
    # t 分布双侧 p 值恒等式：p = I_{df/(df+t²)}(df/2, 1/2)
    x = df / (df + t_stat * t_stat)
    p_value = betainc_reg(df / 2.0, 0.5, x)
    return {"t": round(t_stat, 6), "df": round(df, 4),
            "p_value": round(min(1.0, max(0.0, p_value)), 6),
            "mean_a": round(mean_a, 6), "mean_b": round(mean_b, 6),
            "var_a": round(var_a, 6), "var_b": round(var_b, 6),
            "n_a": n_a, "n_b": n_b}


class ExperimentRunner:
    """A/B 对照实验运行器：variants[name] = 产生一次指标读数的可调用。

    控制变量约定：同一 runner 内所有变体面对的任务集/预算/口径一致，
    变体内部自行固定随机种子；采集方向（越大越好/越小越好）由
    higher_is_better 显式声明，避免"显著"被误读成"更好"。
    """

    def __init__(self, variants: dict[str, Callable[[], float]],
                 repeats: int = 30, *, higher_is_better: bool = True,
                 alpha: float = 0.05):
        if len(variants) < 2:
            raise ValueError("至少需要两个变体才能做 A/B 对照")
        self.variants = dict(variants)
        self.repeats = max(2, int(repeats))
        self.higher_is_better = higher_is_better
        self.alpha = alpha
        self.samples: dict[str, list[float]] = {}

    def run(self) -> dict:
        """跑满 repeats 次，采集每个变体的样本（异常如实记录不静默吞掉）。"""
        self.samples = {}
        errors: dict[str, str] = {}
        for name, fn in self.variants.items():
            samples: list[float] = []
            for _ in range(self.repeats):
                try:
                    samples.append(float(fn()))
                except Exception as e:  # noqa: BLE001 —— 单次失败计入错误清单
                    errors.setdefault(name, f"{type(e).__name__}: {e}"[:200])
            self.samples[name] = samples
        result: dict = {"repeats": self.repeats, "samples": {},
                        "errors": errors, "comparisons": {}}
        for name, samples in self.samples.items():
            result["samples"][name] = {
                "n": len(samples),
                "mean": round(statistics.fmean(samples), 6) if samples else None,
                "stdev": round(statistics.stdev(samples), 6)
                if len(samples) > 1 else None,
            }
        names = list(self.variants)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                key = f"{names[i]}_vs_{names[j]}"
                result["comparisons"][key] = self.compare(names[i], names[j])
        return result

    def compare(self, name_a: str, name_b: str) -> dict:
        """两组样本做 Welch 检验 + 实用显著性（效应量）结论。"""
        a, b = self.samples.get(name_a, []), self.samples.get(name_b, [])
        test = welch_t_test(a, b)
        if "error" in test:
            return {"variant_a": name_a, "variant_b": name_b, **test}
        diff = test["mean_a"] - test["mean_b"]
        rel = diff / abs(test["mean_b"]) if test["mean_b"] else None
        significant = test["p_value"] < self.alpha
        better = None
        if significant and diff != 0:
            a_wins = diff > 0
            better = name_a if a_wins == self.higher_is_better else name_b
        return {"variant_a": name_a, "variant_b": name_b,
                "diff": round(diff, 6),
                "rel_diff": round(rel, 6) if rel is not None else None,
                "p_value": test["p_value"], "alpha": self.alpha,
                "significant": significant, "better": better,
                "higher_is_better": self.higher_is_better,
                "t": test["t"], "df": test["df"]}
