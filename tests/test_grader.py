# -*- coding: utf-8 -*-
"""测试：专职评测 Agent（eval/grader）——规则重算/伪造拦截/维度钳位/集成。"""
import json
import sys
from pathlib import Path

import pytest

from eval.grader import (DIMENSIONS, GraderError, dimension_means,
                         finalize_grading, grade_report)
from src.llm.base import ChatResult
from tests._s4_pipeline_brain import S4Brain

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _good_reply(**overrides):
    payload = {
        "dimensions": {name: {"score": 5, "rationale": "满足要求",
                              "cited_evidence_ids": ["E-001", "E-002"]}
                       for name in DIMENSIONS},
        "issues": [], "fabrication_flags": [], "overall_verdict": "accept"}
    for key, value in overrides.items():
        if key.startswith("dim_"):
            payload["dimensions"][key[4:]] = value
        else:
            payload[key] = value
    return json.dumps(payload, ensure_ascii=False)


class GraderBrain:
    model_name = "grader-stub"
    run_mode = "mock"
    provider = "stub"

    def __init__(self, reply):
        self._reply = reply
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        return ChatResult(content=self._reply if isinstance(self._reply, str)
                          else self._reply())


def _machine(ids=("E-001", "E-002")):
    return {"citation_tokens": 3, "unresolved_citations": 0,
            "cited_ids": list(ids), "forbidden_claim_hits": 0}


def test_grader_accept_and_meta():
    machine = _machine()
    result = finalize_grading(json.loads(_good_reply()), machine_checks=machine,
                              ledger_meta={"mode": "mock", "model": "x"},
                              independence="grader-stub",
                              writer_model="writer-stub")
    assert result["grader"] == "agent" and result["human_confirmed"] is False
    assert result["computed_verdict"] == "accept"
    assert result["meta"]["independence"] == "different"
    assert all(result["dimensions"][name]["score"] == 5 for name in DIMENSIONS)


def test_fabrication_flag_forces_fail_even_with_high_scores():
    machine = _machine()
    result = finalize_grading(
        json.loads(_good_reply(fabrication_flags=["报告中出现虚构来源网址"])),
        machine_checks=machine, ledger_meta={}, independence="g",
        writer_model="w")
    assert result["computed_verdict"] == "fail"


def test_low_score_yields_draft_and_missing_dimension_fails():
    machine = _machine()
    payload = json.loads(_good_reply(dim_correctness={"score": 3,
                                                      "rationale": "部分错误",
                                                      "cited_evidence_ids": []}))
    result = finalize_grading(payload, machine_checks=machine, ledger_meta={},
                              independence="g", writer_model="w")
    assert result["computed_verdict"] == "draft"
    del payload["dimensions"]["structure"]
    result = finalize_grading(payload, machine_checks=machine, ledger_meta={},
                              independence="g", writer_model="w")
    assert result["computed_verdict"] == "fail"
    assert "缺少维度" in result["dimensions"]["structure"].get("problem", "")


def test_grader_citing_fake_id_is_caught_by_program():
    machine = _machine(["E-001"])
    payload = json.loads(_good_reply())
    result = finalize_grading(payload, machine_checks=machine, ledger_meta={},
                              independence="g", writer_model="w")
    assert result["program_problems"]
    assert any("不存在的 id" in p for p in result["program_problems"])
    assert result["computed_verdict"] != "accept"


def test_score_clamp_and_means():
    machine = _machine()
    a = finalize_grading(json.loads(_good_reply(dim_completeness={"score": 7})),
                         machine_checks=machine, ledger_meta={},
                         independence="g", writer_model="w")
    assert a["dimensions"]["completeness"]["score"] is None  # 越界按缺失处理
    b = finalize_grading(json.loads(_good_reply()), machine_checks=machine,
                         ledger_meta={}, independence="g", writer_model="w")
    means = dimension_means([b, b])
    assert means["correctness"] == 5.0


def test_grader_unparseable_twice_raises_with_context(tmp_path):
    class BadBrain(GraderBrain):
        model_name = "bad-grader"
        run_mode = "mock"

        def __init__(self):
            super().__init__("这不是JSON")
    task = {"request": "写报告", "sections": ["A"], "facts": [],
            "forbidden_claims": [], "expected_outcome": "final", "source_ids": ["s01"]}
    with pytest.raises(GraderError, match="JSON"):
        grade_report(task=task, goal="写报告", source_texts=[],
                     final_report="# 报告", evidence=[],
                     machine_checks=_machine(), llm=BadBrain(),
                     workspace_root=tmp_path, mode="mock")


def test_business_eval_inline_grader_with_separate_llm(tmp_path):
    from eval.business_eval import run_business_eval
    writer = S4Brain()

    class GoodGrader(GraderBrain):
        model_name = "grader-pro-stub"

        def __init__(self):
            super().__init__(_good_reply())
    report = run_business_eval(workspace_root=tmp_path / "ws", mode="mock",
                               llm=writer, repeats=1, fault_rounds=1,
                               task_filter="o01", out_dir=tmp_path / "out",
                               grader_llm=GoodGrader())
    record = report["records"][0]
    assert record["grader"]["computed_verdict"] == "accept"
    assert report["grader"]["graded"] == 1
    assert report["grader"]["grader_accept"] == 1
    assert report["grader"]["human_confirmed"] is False
    # 评分者与作者是不同的模型实例/模型名
    assert record["grader"]["meta"]["writer_model"] == "s4-stub"
    assert record["grader"]["meta"]["grader_model"] == "grader-pro-stub"
    assert record["grader"]["meta"]["independence"] == "different"
