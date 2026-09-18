# -*- coding: utf-8 -*-
"""
eval/auto_optimizer.py —— 评估结果 → 运行参数的受控反馈闭环（AutoOptimizer）

闭环但**不是无界自动调参**。三道护栏：
1. 白名单：只允许调整登记在 PARAM_BOUNDS 里的参数（context_budget /
   max_output_tokens / max_calls），费用上限（max_cost）永远不在白名单——
   花钱的事必须人决定；
2. 硬边界：任何调整先夹到 PARAM_BOUNDS 的 (min, max) 区间内；
3. 可审计：默认 dry_run 只产出 patch 不落盘；显式 apply 才生效，且每轮
   最多 MAX_CHANGES_PER_CYCLE 项，全部写入带时间戳的日志（改前/改后/依据）。

工作方式：analyze(指标快照, 当前参数) → [建议]；apply(建议, 当前参数) → 新参数。
规则由 OptimizationRule 数据驱动（指标阈值 → 参数系数），可以按项目增删。
指标名使用展平路径（如 "latency.latency_p95_s"），与 eval 报告的 JSON 结构对应。
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from eval.drift import flatten_metrics

# 白名单 + 硬边界：闭环唯一允许触碰的参数
PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "context_budget": (500, 20000),
    "max_output_tokens": (256, 200000),
    "max_calls": (1, 200),
}
MAX_CHANGES_PER_CYCLE = 3
LOG_DIR = Path(__file__).resolve().parent / "reports" / "auto_optimizer"


@dataclass(frozen=True)
class AdjustmentRule:
    """一条规则：指标越过阈值 → 参数按 factor 相对调整（>1 上调，<1 下调）。"""

    rule_id: str
    metric: str            # 展平指标路径
    below: float | None = None   # 指标低于该值触发（质量类：越小越坏）
    above: float | None = None   # 指标高于该值触发（成本/时延类：越大越坏）
    param: str = ""
    factor: float = 1.0
    reason: str = ""

    def triggered(self, value: float) -> bool:
        if self.below is not None and value < self.below:
            return True
        if self.above is not None and value > self.above:
            return True
        return False


DEFAULT_RULES: tuple[AdjustmentRule, ...] = (
    AdjustmentRule(
        rule_id="raise_context_on_truncation",
        metric="context.truncated_ratio", above=0.20,
        param="context_budget", factor=1.15,
        reason="上下文截断占比过高：提高 context_budget 15%，减少资料被切"),
    AdjustmentRule(
        rule_id="raise_calls_on_budget_stop",
        metric="execution.budget_stopped_ratio", above=0.10,
        param="max_calls", factor=1.20,
        reason="预算中途停止比例过高：放宽 max_calls 20%，减少半途草稿"),
    AdjustmentRule(
        rule_id="lower_tokens_on_slow_p95",
        metric="latency.latency_p95_s", above=400.0,
        param="max_output_tokens", factor=0.90,
        reason="p95 时延过长：压缩 max_output_tokens 10%，压尾延迟"),
)


class AutoOptimizer:
    """分析指标 → 产生参数建议 →（显式）受控应用。"""

    def __init__(self, rules: tuple[AdjustmentRule, ...] = DEFAULT_RULES,
                 *, bounds: dict[str, tuple[float, float]] | None = None,
                 log_dir: Path = LOG_DIR):
        self.rules = tuple(rules)
        self.bounds = dict(PARAM_BOUNDS if bounds is None else bounds)
        self.log_dir = log_dir

    # ---- 分析：指标 + 当前参数 → 建议 ----
    def analyze(self, metrics: dict,
                params: dict | None = None) -> list[dict]:
        flat = flatten_metrics(metrics)
        params = dict(params or {})
        proposals: list[dict] = []
        for rule in self.rules:
            if rule.param not in self.bounds:
                continue                       # 白名单外的参数直接跳过
            if rule.metric not in flat:
                continue                       # 报告里没有该指标不瞎猜
            value = flat[rule.metric]
            if not rule.triggered(value):
                continue
            current = float(params.get(rule.param, self.bounds[rule.param][0]))
            proposed = self._clamp(current * rule.factor, rule.param)
            if math.isclose(proposed, current, rel_tol=1e-9):
                continue                       # 已在边界上，调不动了
            proposals.append({
                "rule_id": rule.rule_id, "metric": rule.metric,
                "metric_value": value, "param": rule.param,
                "current": current, "proposed": proposed,
                "reason": rule.reason})
        return proposals[:MAX_CHANGES_PER_CYCLE]

    # ---- 应用：建议 + 当前参数 → 新参数（默认 dry_run）----
    def apply(self, proposals: list[dict], params: dict, *,
              dry_run: bool = True) -> dict:
        capped = proposals[:MAX_CHANGES_PER_CYCLE]
        patch: dict[str, float] = {}
        applied: list[dict] = []
        new_params = dict(params)
        for proposal in capped:
            param = proposal.get("param", "")
            if param not in self.bounds or param in patch:
                # 白名单外跳过；同一参数每轮只调一次（按规则顺序首条生效），
                # 其余计入 skipped —— 防止多条规则叠加出界
                continue
            value = self._clamp(float(proposal.get("proposed", 0)), param)
            patch[param] = value
            new_params[param] = value
            applied.append(proposal)
        outcome = {"applied": not dry_run, "dry_run": dry_run,
                   "patch": patch, "new_params": new_params,
                   "skipped": len(proposals) - len(applied)}
        if not dry_run and patch:
            outcome["log_path"] = str(self._write_log(applied, patch))
        return outcome

    # ---- 内部 ----
    def _clamp(self, value: float, param: str) -> float:
        low, high = self.bounds[param]
        return round(min(high, max(low, value)), 4)

    def _write_log(self, proposals: list[dict], patch: dict) -> Path:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.log_dir / f"optimizer_{stamp}.json"
        path.write_text(json.dumps(
            {"applied_at": datetime.now().isoformat(timespec="seconds"),
             "changes": patch, "basis": proposals},
            ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def summarize_series(values: list[float]) -> dict:
    """给闭环喂指标用的小工具：一批次运行值 → 汇总快照（均值/最差/波动）。"""
    if not values:
        return {"n": 0}
    return {"n": len(values),
            "mean": round(statistics.fmean(values), 6),
            "worst": round(min(values), 6),
            "stdev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0}


# ---- CLI：对一份真实指标报告跑一轮闭环分析（默认 dry-run）----
def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="AutoOptimizer：指标报告 → 白名单参数调整建议（默认 dry-run）")
    parser.add_argument("--metrics", required=True, help="指标报告 JSON（自动展平）")
    parser.add_argument("--params", help="当前参数 JSON；缺省用项目默认值")
    parser.add_argument("--rules", help="规则 JSON（AdjustmentRule 字段列表）；缺省用内置规则")
    parser.add_argument("--apply", action="store_true",
                        help="显式应用（写审计日志）；缺省只做 dry-run")
    parser.add_argument("--out", help="结果 JSON 落盘路径（可省略）")
    args = parser.parse_args(argv)

    params = {"context_budget": 6000, "max_output_tokens": 8192, "max_calls": 12}
    if args.params:
        params.update(json.loads(Path(args.params).read_text(encoding="utf-8")))
    rules = DEFAULT_RULES
    if args.rules:
        rules = tuple(AdjustmentRule(**r) for r in
                      json.loads(Path(args.rules).read_text(encoding="utf-8")))

    optimizer = AutoOptimizer(rules=rules)
    metrics = json.loads(Path(args.metrics).read_text(encoding="utf-8"))
    proposals = optimizer.analyze(metrics, params=params)
    outcome = optimizer.apply(proposals, params, dry_run=not args.apply)
    result = {"metrics_file": args.metrics, "dry_run": not args.apply,
              "proposals": proposals, "patch": outcome["patch"],
              "new_params": outcome["new_params"],
              "skipped": outcome["skipped"],
              "log_path": outcome.get("log_path", ""),
              "generated_at": datetime.now().isoformat(timespec="seconds")}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
