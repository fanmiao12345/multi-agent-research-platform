# -*- coding: utf-8 -*-
"""实测脚本冒烟：三个口径都能离线跑通、读数在合理区间（报告不在此落盘）。"""
import json

import eval.resume_metrics as rm


def test_skill_token_savings_structure():
    result = rm.measure_skill_token_savings()
    assert result["skills_count"] > 0
    assert result["experiments"] == result["skills_count"]   # 每技能各测一次
    assert 0.0 <= result["system_prompt_savings"] <= 1.0
    assert 0.0 <= result["routing_stage_savings"] <= 1.0
    assert result["system_prompt_savings_min"] <= result["system_prompt_savings"] \
        <= result["system_prompt_savings_max"]
    assert result["progressive_tokens_mean"] < result["naive_tokens"]  # 渐进一定更省
    assert all("savings_when_hit" in p for p in result["per_skill"])


def test_parallel_speedup_beats_sequential():
    result = rm.measure_parallel_speedup(sleep=0.02, repeats=3)
    assert result["time_reduction"] > 0.2        # 3 并发 + 6 任务应明显快于顺序
    assert result["fanout_mean_s"] < result["sequential_mean_s"]
    assert result["speedup_x"] > 1.0
    assert 0.0 <= result["time_reduction_median"] <= 1.0


def test_parallel_speedup_jitter_scenario_still_wins():
    result = rm.measure_parallel_speedup(sleep=0.05, repeats=5, jitter=True)
    assert result["jitter"] is True
    assert result["time_reduction"] > 0.2        # 异质时长下提速结论仍成立
    assert result["fanout_mean_s"] < result["sequential_mean_s"]


def test_prompt_cache_stability_structure_and_honest_reading():
    result = rm.measure_prompt_cache_stability(turns=4)
    assert 0.0 <= result["memory_in_system_prefix_share"] <= 1.0
    assert 0.0 <= result["memory_in_user_prefix_share"] <= 1.0
    # 简历主张中站得住的部分：system 前缀逐字节稳定
    assert result["system_prefix_byte_stable"] is True
    # 诚实读数：当前管线历史窗口恒为 2 条（O-15），两种位置占比都低、增益≈0
    assert abs(result["stable_prefix_gain"]) < 0.2
    assert "O-15" in result["note"]


def test_cross_session_hit_rate(tmp_path):
    result = rm.measure_cross_session_hit_rate(
        store_path=tmp_path / "reuse_mem.json", bootstrap=50)
    assert result["cases"] == len(rm.REUSE_CASES)
    assert result["vector_backend"] in ("sqlite_vss", "hashing_cosine")
    assert 0.0 <= result["top1_hit_rate"] <= 1.0
    assert result["topk_hit_rate"] >= result["top1_hit_rate"]  # top-k 不会更差
    # bootstrap 统计口径：均值与 95% 置信区间
    assert result["bootstrap_resamples"] == 50
    assert result["top1_ci95"][0] <= result["top1_mean"] <= result["top1_ci95"][1]
    assert result["top3_ci95"][0] <= result["top3_mean"] <= result["top3_ci95"][1]


def test_run_all_and_render(tmp_path):
    report = rm.run_all(sleep=0.01, repeats=2)
    md = rm.render_md(report)
    assert "耗时降低" in md and "命中率" in md
    # JSON 可序列化（报告落盘的前提）
    json.dumps(report, ensure_ascii=False)
