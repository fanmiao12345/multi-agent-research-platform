# -*- coding: utf-8 -*-
"""
eval/drift.py —— 指标漂移检测与阈值告警（Drift Detection & Alerting）

把"当前批次的指标 JSON"和"冻结基线"逐指标对比：
- 绝对阈值 abs：|当前-基线| > abs 即告警（适合 0~1 比例类指标）
- 相对阈值 rel：|当前-基线|/基线 > rel 即告警（适合费用/耗时类）

只比较两侧都存在且可数值化的叶子指标（嵌套 dict 自动展平，
列表/字符串跳过）；新增指标单独列出不算漂移，基线指标消失算 drift
（指标丢失往往意味着评测口径变了，必须人看得见）。

用法：
    python -m eval.drift --baseline eval/reports/B1_BASELINE.json \
                         --current  eval/reports/metrics_all.json \
                         --abs 0.05 --rel 0.20 --out eval/reports/drift_report.json
供 q2_summary / q3_compare 之后挂一道自动体检：status=drift 即需要人看。
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path


def flatten_metrics(data: dict, prefix: str = "") -> dict[str, float]:
    """把嵌套指标 dict 展平成 {点路径: float}；只保留可数值化的叶子。"""
    out: dict[str, float] = {}
    for key, value in (data or {}).items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(flatten_metrics(value, path))
        elif isinstance(value, bool):
            out[path] = float(value)
        elif isinstance(value, (int, float)):
            out[path] = float(value)
        # 列表/字符串等不进漂移比较（口径太容易歧义，宁可漏报不误报）
    return out


def check_drift(baseline: dict, current: dict, *,
                abs_threshold: float = 0.05,
                rel_threshold: float = 0.20,
                per_metric: dict | None = None) -> dict:
    """逐指标对比，返回 {status, alerts, missing_in_current, added, details}。

    per_metric：{指标名: {"abs": x, "rel": y}} 覆盖全局阈值（可只给其中一维）。
    direction 标明恶化方向：higher_is_worse / lower_is_worse / none（由阈值触发
    方向自动推断：指标变大且触发了 higher_worse 类阈值 → 记恶化）。
    """
    per_metric = per_metric or {}
    base = flatten_metrics(baseline)
    curr = flatten_metrics(current)

    alerts: list[dict] = []
    details: list[dict] = []
    for metric in sorted(set(base) & set(curr)):
        b, c = base[metric], curr[metric]
        change = c - b
        rel_change = (change / abs(b)) if b != 0 else (0.0 if change == 0 else None)
        th = per_metric.get(metric, {})
        abs_lim = th.get("abs", abs_threshold)
        rel_lim = th.get("rel", rel_threshold)
        breached = False
        if abs_lim is not None and abs(change) > abs_lim:
            breached = True
        if rel_lim is not None and rel_change is not None and abs(rel_change) > rel_lim:
            breached = True
        if breached:
            direction = ("higher_is_worse" if change > 0 else "lower_is_worse")
            alerts.append({
                "metric": metric, "baseline": b, "current": c,
                "change": round(change, 6),
                "rel_change": round(rel_change, 6) if rel_change is not None else None,
                "thresholds": {"abs": abs_lim, "rel": rel_lim},
                "direction": direction,
                "alert": f"[漂移告警] {metric}: {b} → {c}"
                         f"（Δ={change:+.4g}"
                         + (f"，相对 {rel_change:+.1%}" if rel_change is not None else "")
                         + "）"})
        details.append({"metric": metric, "baseline": b, "current": c,
                        "change": round(change, 6), "drift": breached})

    missing = sorted(set(base) - set(curr))
    added = sorted(set(curr) - set(base))
    status = "drift" if (alerts or missing) else "ok"
    return {"status": status, "alerts": alerts,
            "missing_in_current": missing, "added": added,
            "compared": len(details), "details": details}


def detect_trend(history: list[dict], *, window: int = 5, k: float = 2.0,
                 decline_runs: int = 3) -> dict:
    """滑动均值趋势检测：只看指标序列本身，不需要人工基线。

    history：按时间从旧到新的多份指标快照（每份是指标 dict，自动展平）。
    对每个贯穿全程的指标做两件事：
    1. 滑动均值离群——最近 window 个批次的均值/样本标准差为参照，
       最新值偏离超过 k·std 即告警（z 分数越界，突发退化）；
    2. 持续单边漂移——最近 decline_runs 次变化全部同方向（持续变差趋势，
       往往在离群之前就能看到，适合缓慢劣化）。
    """
    if len(history) < 2:
        return {"status": "ok", "alerts": [], "details": [],
                "note": f"批次不足（{len(history)} < 2），无法做趋势检测"}
    series = [flatten_metrics(h) for h in history]
    keys = set(series[0])
    for s in series[1:]:
        keys &= set(s)
    alerts: list[dict] = []
    details: list[dict] = []
    for metric in sorted(keys):
        values = [s[metric] for s in series]
        latest = values[-1]
        entry: dict = {"metric": metric, "latest": latest,
                       "batches": len(values)}
        # 通道 1：滑动均值离群（突发退化）
        if len(values) >= window + 1:
            win = values[-(window + 1):-1]
            mean = statistics.fmean(win)
            std = statistics.stdev(win) if window > 1 else 0.0
            entry["window_mean"] = round(mean, 6)
            entry["window_std"] = round(std, 6)
            if std > 0 and abs(latest - mean) > k * std:
                z = (latest - mean) / std
                entry["z"] = round(z, 3)
                alerts.append({
                    "metric": metric, "kind": "spike",
                    "latest": latest, "window_mean": round(mean, 6),
                    "z": round(z, 3), "threshold_k": k,
                    "alert": f"[趋势告警·突发] {metric}: {latest} 偏离滑动均值 "
                             f"{round(mean, 6)} 达 {round(z, 2)}σ（> {k}σ）"})
        # 通道 2：持续单边漂移（缓慢劣化）
        tail = values[-(decline_runs + 1):]
        if len(tail) == decline_runs + 1:
            diffs = [b - a for a, b in zip(tail, tail[1:])]
            if all(d < 0 for d in diffs) or all(d > 0 for d in diffs):
                direction = "上升" if diffs[0] > 0 else "下降"
                entry["sustained_direction"] = direction
                alerts.append({
                    "metric": metric, "kind": "sustained",
                    "latest": latest, "direction": direction,
                    "runs": decline_runs,
                    "alert": f"[趋势告警·持续] {metric}: 连续 {decline_runs} 批"
                             f"单调{direction}（{tail[0]} → {latest}）"})
        details.append(entry)
    return {"status": "drift" if alerts else "ok", "alerts": alerts,
            "details": details, "batches": len(history),
            "window": window, "k": k, "decline_runs": decline_runs}


def render_md(report: dict, *, baseline_path: str = "", current_path: str = "") -> str:
    lines = ["# 指标漂移检测报告",
             f"- 时间：{report.get('generated_at', '')}",
             f"- 基线：{baseline_path}　当前：{current_path}",
             f"- 结论：**{report['status']}**（对比 {report['compared']} 项指标，"
             f"{len(report['alerts'])} 条告警，{len(report['missing_in_current'])} 项基线指标丢失）",
             ""]
    if report["alerts"]:
        lines += ["## 告警", ""]
        lines += [a["alert"] for a in report["alerts"]]
        lines.append("")
    if report["missing_in_current"]:
        lines += ["## 基线有而当前缺失（口径变化？）", ""]
        lines += [f"- {m}" for m in report["missing_in_current"]]
        lines.append("")
    if report["added"]:
        lines += [f"## 当前新增指标（{len(report['added'])} 项，不计漂移）", ""]
        lines += [f"- {m}" for m in report["added"][:20]]
        if len(report["added"]) > 20:
            lines.append(f"- …共 {len(report['added'])} 项")
    return "\n".join(lines) + "\n"


def render_trend_md(report: dict, *, history_path: str = "") -> str:
    lines = ["# 指标趋势检测报告（滑动均值 + 持续单边漂移）",
             f"- 时间：{report.get('generated_at', '')}",
             f"- 历史批次：{history_path}（{report.get('batches', 0)} 批）",
             f"- 结论：**{report['status']}**（{len(report['alerts'])} 条趋势告警）",
             ""]
    if report["alerts"]:
        lines += [a["alert"] for a in report["alerts"]]
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="指标漂移检测与阈值告警")
    parser.add_argument("--baseline", help="基线指标 JSON（与 --history 二选一）")
    parser.add_argument("--current", help="当前指标 JSON（基线模式必填）")
    parser.add_argument("--history", help="历史批次指标 JSON 的 glob（趋势模式，旧→新排序）")
    parser.add_argument("--window", type=int, default=5, help="滑动均值窗口（默认 5）")
    parser.add_argument("--abs", type=float, default=0.05, help="全局绝对阈值（默认 0.05）")
    parser.add_argument("--rel", type=float, default=0.20, help="全局相对阈值（默认 0.20）")
    parser.add_argument("--thresholds", help="逐指标阈值 JSON {metric:{abs,rel}}")
    parser.add_argument("--out", help="报告 JSON 输出路径（可省略）")
    parser.add_argument("--no-report", action="store_true",
                        help="只打印与返回退出码，不落盘（测试用）")
    args = parser.parse_args(argv)

    if args.history:
        import glob
        files = sorted(Path(f) for f in glob.glob(args.history))
        if len(files) < 2:
            parser.error(f"--history 需要至少 2 个批次文件，匹配到 {len(files)} 个")
        history = [json.loads(p.read_text(encoding="utf-8")) for p in files]
        report = detect_trend(history, window=args.window)
        report["generated_at"] = datetime.now().isoformat(timespec="seconds")
        md = render_trend_md(report, history_path=args.history)
        print(md)
        if args.out and not args.no_report:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            out.with_suffix(".md").write_text(md, encoding="utf-8")
        return 0 if report["status"] == "ok" else 1

    if not args.baseline or not args.current:
        parser.error("基线模式需要 --baseline 和 --current（或用 --history 走趋势模式）")
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    current = json.loads(Path(args.current).read_text(encoding="utf-8"))
    per_metric = {}
    if args.thresholds:
        per_metric = json.loads(Path(args.thresholds).read_text(encoding="utf-8"))

    report = check_drift(baseline, current, abs_threshold=args.abs,
                         rel_threshold=args.rel, per_metric=per_metric)
    report["generated_at"] = datetime.now().isoformat(timespec="seconds")
    report["baseline_path"] = args.baseline
    report["current_path"] = args.current

    md = render_md(report, baseline_path=args.baseline, current_path=args.current)
    print(md)
    if args.out and not args.no_report:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        out.with_suffix(".md").write_text(md, encoding="utf-8")
    # 退出码：ok=0，drift=1 —— 可以直接挂进 CI / 脚本链
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
