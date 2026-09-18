# -*- coding: utf-8 -*-
"""漂移检测：展平 / 阈值触发 / 逐指标覆盖 / 丢失指标 / 报告渲染 / CLI 退出码。"""
import json

import pytest

import eval.drift as drift

BASELINE = {
    "delivery": {"accept_rate": 0.7, "citation_ok": 1.0},
    "latency_p50_s": 195,
    "cost_usd": 4.71,
    "note": "文本字段不参与",
}
CURRENT_OK = {
    "delivery": {"accept_rate": 0.72, "citation_ok": 1.0},
    "latency_p50_s": 200,
    "cost_usd": 4.9,
    "new_metric": 3,                        # 新增不算漂移
}


def test_flatten_metrics_only_numeric_leaves():
    flat = drift.flatten_metrics(BASELINE)
    assert flat["delivery.accept_rate"] == 0.7
    assert flat["latency_p50_s"] == 195
    assert "note" not in flat


def test_no_drift_within_thresholds():
    report = drift.check_drift(BASELINE, CURRENT_OK, abs_threshold=None,
                               rel_threshold=0.2)
    assert report["status"] == "ok"
    assert report["alerts"] == []
    assert report["compared"] == 4                    # 双侧共有的数值叶子
    assert report["added"] == ["new_metric"]


def test_drift_alerts_on_relative_breach():
    current = dict(CURRENT_OK, latency_p50_s=280)          # +43.6% > 20%
    report = drift.check_drift(BASELINE, current, abs_threshold=None,
                               rel_threshold=0.2)
    assert report["status"] == "drift"
    alert = report["alerts"][0]
    assert alert["metric"] == "latency_p50_s"
    assert alert["direction"] == "higher_is_worse"


def test_per_metric_threshold_override():
    current = dict(CURRENT_OK, cost_usd=6.0)               # +27%，超全局 20%
    report = drift.check_drift(BASELINE, current, abs_threshold=None,
                               rel_threshold=0.2,
                               per_metric={"cost_usd": {"rel": 0.5}})
    assert report["status"] == "ok"                        # 逐指标放宽后不告警
    report2 = drift.check_drift(BASELINE, current, abs_threshold=None,
                                rel_threshold=0.99,
                                per_metric={"cost_usd": {"rel": 0.1}})
    assert report2["status"] == "drift"                    # 收紧后告警


def test_missing_baseline_metric_is_drift():
    current = {k: v for k, v in CURRENT_OK.items() if k != "latency_p50_s"}
    report = drift.check_drift(BASELINE, current)
    assert report["status"] == "drift"
    assert "latency_p50_s" in report["missing_in_current"]


def test_render_md_contains_alerts(tmp_path):
    current = dict(CURRENT_OK, latency_p50_s=300)
    report = drift.check_drift(BASELINE, current, abs_threshold=None,
                               rel_threshold=0.2)
    report["generated_at"] = "2026-09-16T00:00:00"
    md = drift.render_md(report, baseline_path="b.json", current_path="c.json")
    assert "latency_p50_s" in md and "漂移告警" in md


def test_cli_exit_codes(tmp_path, capsys):
    baseline_file = tmp_path / "b.json"
    current_file = tmp_path / "c.json"
    baseline_file.write_text(json.dumps(BASELINE), encoding="utf-8")
    current_file.write_text(json.dumps(CURRENT_OK), encoding="utf-8")

    from eval.drift import main
    rc_ok = main(["--baseline", str(baseline_file), "--current",
                  str(current_file), "--abs", "10", "--rel", "0.2",
                  "--no-report"])
    assert rc_ok == 0

    bad = dict(CURRENT_OK, latency_p50_s=500)
    current_file.write_text(json.dumps(bad), encoding="utf-8")
    rc_drift = main(["--baseline", str(baseline_file), "--current",
                     str(current_file), "--abs", "10", "--rel", "0.2",
                     "--no-report"])
    assert rc_drift == 1


# ---- 趋势检测（滑动均值 + 持续单边漂移）----

def _series(values: list[float], key: str = "score") -> list[dict]:
    """把一列数值包成时间顺序的指标快照（旧→新）。"""
    return [{key: v, "const": 1} for v in values]


def test_trend_stable_series_is_ok():
    report = drift.detect_trend(_series([0.5] * 8), window=5, k=2.0)
    assert report["status"] == "ok"
    assert report["alerts"] == []


def test_trend_spike_alerts_on_sudden_deviation():
    values = [0.50, 0.48, 0.52, 0.49, 0.51, 0.50, 0.49, 0.90]
    report = drift.detect_trend(_series(values), window=5, k=2.0)
    spikes = [a for a in report["alerts"] if a["kind"] == "spike"]
    assert report["status"] == "drift"
    assert len(spikes) == 1
    assert spikes[0]["metric"] == "score" and spikes[0]["z"] > 2


def test_trend_sustained_decline_alerts():
    values = [0.90, 0.82, 0.74, 0.66, 0.58, 0.50]   # 连续 5 次下降
    report = drift.detect_trend(_series(values), window=5, k=2.0)
    sustained = [a for a in report["alerts"] if a["kind"] == "sustained"]
    assert len(sustained) == 1
    assert sustained[0]["direction"] == "下降"


def test_trend_too_few_batches_returns_ok_with_note():
    report = drift.detect_trend(_series([0.5]))
    assert report["status"] == "ok" and "note" in report


def test_trend_ignores_metrics_not_in_every_batch():
    history = [{"a": 1.0, "b": 2.0}, {"a": 1.0}, {"a": 1.0, "b": 5.0}]
    report = drift.detect_trend(history, window=1, k=2.0)
    assert [d["metric"] for d in report["details"]] == ["a"]


def test_cli_history_mode(tmp_path):
    values = [0.50, 0.48, 0.52, 0.49, 0.51, 0.50, 0.49, 0.90]
    for i, v in enumerate(values):
        (tmp_path / f"batch_{i:02d}.json").write_text(
            json.dumps({"score": v}), encoding="utf-8")
    from eval.drift import main
    rc = main(["--history", str(tmp_path / "batch_*.json"), "--no-report"])
    assert rc == 1                                   # 尖峰漂移 → 退出码 1
