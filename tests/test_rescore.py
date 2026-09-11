# -*- coding: utf-8 -*-
"""S8-A 口径重算测试：三指标分离（执行完成率/预期行为符合率/成品质量达标率）与 partial 单列。"""
from eval.rescore import rescore_report


def _attempt(aid, expected_hint, *, draft_level=None, status="passed", grader_verdict=None,
             dims=None, unresolved=0, tokens=0, fab=0, forbidden=0):
    att = {"id": aid, "category": "organize", "attempt": 1, "task": "t", "status": status,
           "termination_reason": "completed", "estimated_cost_usd": 0.01,
           "machine_checks": {"citation_tokens": tokens, "unresolved_citations": unresolved,
                              "forbidden_claim_hits": forbidden, "required_sections": 1,
                              "section_hits": 1, "fact_hits": 1, "fact_total": 1},
           "chain_hard_checks": {}, "unknown_usage_calls": 0}
    if draft_level is not None:
        att["draft_level"] = draft_level
    if grader_verdict is not None:
        att["grader"] = {"grader": "agent", "human_confirmed": False,
                         "overall_verdict": grader_verdict, "computed_verdict": grader_verdict,
                         "dimensions": {k: {"score": v, "rationale": "", "cited_evidence_ids": []}
                                        for k, v in (dims or {}).items()},
                         "issues": [], "fabrication_flags": (["x"] * fab) if fab else [],
                         "program_problems": [], "meta": {}}
    return att


def _report(attempts):
    return {"meta": {"name": "synthetic_v1", "mode": "real", "generated_at": "2026-09-10",
                     "versions": {"code_revision": "test0"}},
            "totals": {"attempts_total": len(attempts)}, "results": attempts}


EXPECT = {"a1": "final", "a2": "final", "a3": "draft", "a4": "unable", "a5": "final", "a6": "partial"}


def test_three_metrics_split():
    report = _report([
        # a1: 预期成品，链内 accepted，独立评测 accept —— 全对
        _attempt("a1", "final", draft_level="accepted", grader_verdict="accept",
                 dims={"correctness": 5, "structure": 5, "citations": 5, "completeness": 5},
                 tokens=3, unresolved=0),
        # a2: 预期成品，链内 accepted，独立评测 draft —— 预期不符质量，计入质量口径
        _attempt("a2", "final", draft_level="accepted", grader_verdict="draft",
                 dims={"correctness": 3, "structure": 4, "citations": 4, "completeness": 4},
                 fab=1, forbidden=1, tokens=2, unresolved=1),
        # a3: 预期草稿，交付草稿 —— 行为符合
        _attempt("a3", "draft", draft_level="draft", grader_verdict="accept"),
        # a4: 预期无法完成，交付 failed —— 行为符合（执行完成）
        _attempt("a4", "unable", draft_level="failed", status="failed", grader_verdict="fail"),
        # a5: 跳过未执行 —— 不计完成
        _attempt("a5", "final", status="not_executed"),
        # a6: partial 预期 —— 单列，不进符合率分母
        _attempt("a6", "partial", draft_level="accepted", grader_verdict="accept"),
    ])
    res = rescore_report(report, EXPECT)
    t = res["totals"]
    # 执行完成：5/6（a5 跳过）
    assert t["attempts_total"] == 6 and t["executed"] == 5
    assert t["execution_completion_rate"] == round(5 / 6, 4)
    # 预期行为符合：a1 对、a2 交付成品 vs 预期成品 = 对……等等：a2 交付 accepted=成品，预期 final=成品，符合；
    # a3 草稿/草稿对；a4 failed/unable 对；a6 单列。=> 4/4 符合
    assert t["conformance_denominator"] == 4 and t["expected_match"] == 4
    assert abs(t["expected_behavior_conformance_rate"] - 1.0) < 1e-9
    assert t["partial_single_listed"] == ["a6"]
    # 成品质量：链内 accepted = {a1, a2, a6}，独立 accept = {a1, a6} => 2/3
    assert sorted(t["chain_accepted_ids"]) == ["a1", "a2", "a6"]
    assert t["quality_grader_accept_rate"] == round(2 / 3, 4)
    # 引用可定位：a1 3/0 + a2 2/1 => 1 - 1/5 = 0.8（只统计链内 accepted 且有引用的）
    assert abs(t["citation_locate_rate"] - 0.8) < 1e-9
    # 伪造/禁语只统计链内 accepted：a2 有 1 条伪造标记、1 次禁语命中
    assert t["chain_accepted_with_fabrication_flags"] == 1
    assert t["chain_accepted_forbidden_hits"] == 1
    # 诚实标记必须随报告输出
    assert t["human_confirmed"] is False
    assert "开卷" in t["caveats"][1] or any("开卷" in c for c in t["caveats"])


def test_zero_citations_is_unknown_not_perfect():
    report = _report([_attempt("a1", "final", draft_level="accepted", grader_verdict="accept",
                               tokens=0, unresolved=0)])
    res = rescore_report(report, {"a1": "final"})
    assert res["totals"]["citation_locate_rate"] is None  # 未知不记 100%


def test_level_mismatch_recorded():
    report = _report([_attempt("a1", "draft", draft_level="accepted", grader_verdict="accept")])
    res = rescore_report(report, {"a1": "draft"})
    t = res["totals"]
    assert t["expected_match"] == 0 and t["conformance_denominator"] == 1
    assert res["rows"][0]["level_match"] is False


def test_unable_delivery_level_mapping():
    """S8 unable 机制：链主动声明无法完成 = 预期 unable 时符合，预期 final 时不符合。"""
    report = _report([
        _attempt("a1", "unable", draft_level="unable", status="failed",
                 grader_verdict="accept"),
        _attempt("a2", "final", draft_level="unable", status="failed",
                 grader_verdict="accept"),
    ])
    res = rescore_report(report, {"a1": "unable", "a2": "final"})
    rows = {r["id"]: r for r in res["rows"]}
    assert rows["a1"]["level_match"] is True and rows["a1"]["delivered_cn"] == "无法完成"
    assert rows["a2"]["level_match"] is False
    t = res["totals"]
    assert t["executed"] == 2                       # unable 也算执行完成（有明确交付）
    assert t["expected_match"] == 1 and t["conformance_denominator"] == 2
    assert t["chain_accepted_ids"] == []            # unable 不算 accepted
