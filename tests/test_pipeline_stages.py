# -*- coding: utf-8 -*-
"""测试：研究写作链阶段与 runner（S3-04~13 / B5）。"""
import json
import re
from pathlib import Path

import pytest

from src.application.pipeline.draft import run_draft_stage
from src.application.pipeline.evidence import (EvidenceStore, collect_citations,
                                               extract_source_evidence, make_id)
from src.application.pipeline.material import (fill_duplicates, render_material,
                                               run_material_stage)
from src.application.pipeline.model import ReviewIssue, StageError
from src.application.pipeline.outline import run_outline_stage
from src.application.pipeline.review import (model_review, program_checks)
from src.application.pipeline.runner import run_research_pipeline
from src.harness.model_gateway import JobLedger, job_scope
from src.llm.base import ChatResult
from src.harness.storage.sources import SourceStore


def _store_with(job_dir, texts):
    store = SourceStore(job_dir)
    for index, text in enumerate(texts, start=1):
        store.add_paste(text, display_index=index)
    return store


TEXTS = [
    "第一段：总量为42%，用于甲方案。\n\n第二段：乙方法通过校验。\n",
    "另一份材料开头。\n\n后续内容提到丙与丁。\n",
]


def test_evidence_stage_locates_quotes_and_drops_bogus():
    class Brain:
        model_name = "stub"
        run_mode = "mock"
        calls = 0

        def chat(self, messages, tools=None):
            Brain.calls += 1
            return ChatResult(content=json.dumps({
                "items": [
                    {"fact": "甲方案占比", "tag": "F", "quote": "总量为42%，用于甲方案。"},
                    {"fact": "乙方法", "tag": "F", "quote": "不存在于原文的摘录"},
                    {"fact": "未知标注", "tag": "X", "quote": "乙方法通过校验。"},
                ]}, ensure_ascii=False))
    items, issues = extract_source_evidence(Brain(), "整理资料", {
        "source_id": "src_x", "title": "t", "display": "d", "text": TEXTS[0]})
    assert len(items) == 1
    assert items[0].quote == "总量为42%，用于甲方案。"
    assert items[0].start >= 0 and items[0].locator.get("paragraph") == 0
    codes = {issue["code"] for issue in issues}
    assert "citation" in codes and "format" in codes
    assert Brain.calls == 1


def test_evidence_store_ids_and_citations():
    store = EvidenceStore(Path("x") / "job")
    assert store.is_id("E-001") and not store.is_id("E-x")
    assert collect_citations("先看[E-001]，再看[E-002][E-001]") == ["E-001", "E-002", "E-001"]
    assert make_id(7) == "E-007"


def test_material_stage_validates_ids_and_forces_conflict_open():
    class Brain:
        model_name = "stub"
        run_mode = "mock"

        def chat(self, messages, tools=None):
            return ChatResult(content=json.dumps({
                "topics": [{"name": "主题", "points": [
                    {"evidence_id": "E-001", "statement": "支持甲"},
                    {"evidence_id": "E-999", "statement": "假证据"},
                    {"evidence_id": "E-002", "statement": ""}]}],
                "conflicts": [{"statement": "甲乙冲突", "evidence_ids": ["E-001", "E-002"],
                               "status": "resolved"}],
                "gaps": [{"question": "缺丙资料", "missing": "来源"}]}, ensure_ascii=False))
    pack, issues = run_material_stage(Brain(), "目标", [
        {"evidence_id": "E-001", "fact": "甲", "tag": "F", "quote": "q",
         "source_id": "s1"},
        {"evidence_id": "E-002", "fact": "乙", "tag": "F", "quote": "q",
         "source_id": "s2"}])
    assert pack.topics[0]["points"] == [{"evidence_id": "E-001", "statement": "支持甲"}]
    assert pack.conflicts[0]["status"] == "open"
    assert any(issue["code"] == "citation" for issue in issues)
    assert any("无依据" in issue["message"] for issue in issues)
    assert pack.gaps == [{"question": "缺丙资料", "missing": "来源"}]
    dupes = fill_duplicates(pack, [{"status": "duplicate", "display": "b",
                                    "duplicate_of": "src_a"},
                                   {"status": "ok", "display": "c"}])
    assert dupes == [{"display": "b", "duplicate_of": "src_a"}]


def test_outline_stage_filters_bad_ids_and_requires_sections():
    class Brain:
        model_name = "stub"
        run_mode = "mock"

        def chat(self, messages, tools=None):
            return ChatResult(content=json.dumps({
                "title": "报告", "sections": [
                    {"heading": "背景", "purpose": "p", "required_evidence": ["E-001", "E-999"],
                     "require_fact_markers": True},
                    {"heading": ""},
                ]}, ensure_ascii=False))
    sections, title, issues = run_outline_stage(
        Brain(), "目标", "素材", {"E-001"})
    assert len(sections) == 1 and title == "报告"
    assert sections[0].required_evidence == ["E-001"]
    assert any(issue["code"] == "citation" for issue in issues)

    class EmptyBrain:
        model_name = "stub"
        run_mode = "mock"

        def chat(self, messages, tools=None):
            return ChatResult(content=json.dumps(
                {"sections": [{"heading": "", "required_evidence": []}]}))
    with pytest.raises(StageError):
        run_outline_stage(EmptyBrain(), "目标", "素材", {"E-001"})


def test_draft_stage_rejects_bad_outputs():
    class NoField:
        model_name = "stub"
        run_mode = "mock"

        def chat(self, messages, tools=None):
            return ChatResult(content='{"x": 1}')

    class Short:
        model_name = "stub"
        run_mode = "mock"

        def chat(self, messages, tools=None):
            return ChatResult(content=json.dumps({"report_markdown": "太短"}))
    with pytest.raises(StageError, match="report_markdown"):
        run_draft_stage(NoField(), "g", [], "t", {})
    with pytest.raises(StageError, match="过短"):
        run_draft_stage(Short(), "g", [], "t", {})


def test_program_checks_citations_coverage_sections_markers():
    sections = []
    from src.application.pipeline.model import OutlineSection
    sections.append(OutlineSection("背景", "b", ["E-001"], require_fact_markers=True))
    sections.append(OutlineSection("结论", "c", ["E-002"]))
    ok_report = "# 报告\n\n## 背景\n\n依据见[E-001]〔事实〕。\n\n## 结论\n\n结论见[E-002]。\n"
    assert program_checks(ok_report, {"E-001", "E-002"}, sections) == []
    bad = ("# 报告\n\n## 背景\n\n无引用。[E-999]\n\n## 结尾\n\n[E-001]\n")
    issues = program_checks(bad, {"E-001"}, sections)
    codes = {issue.code for issue in issues}
    assert {"citation", "coverage", "section"} <= codes


def test_model_review_verdict_override():
    class Brain:
        model_name = "stub"
        run_mode = "mock"

        def chat(self, messages, tools=None):
            return ChatResult(content=json.dumps({
                "issues": [{"severity": "error", "code": "support",
                            "message": "结论一缺证据"}], "verdict": "accepted"}))
    issues, verdict = model_review(Brain(), "g", "# r", "E-001 甲", [])
    assert verdict == "needs_revision"
    assert issues[0].severity == "error"

    class NoJson:
        model_name = "stub"
        run_mode = "mock"

        def chat(self, messages, tools=None):
            return ChatResult(content="这不是JSON")
    with pytest.raises(StageError):
        model_review(NoJson(), "g", "x", "y", [])  # 非 JSON 输出按阶段错误处理


class PipelineBrain:
    """脚本化链上大脑：按系统提示中的阶段标记返回对应 JSON。"""
    model_name = "pipeline-stub"
    run_mode = "mock"
    provider = "stub"

    def __init__(self, mode="good", usage=None):
        self.mode = mode
        self.usage = usage or {"prompt_tokens": 20, "completion_tokens": 8}
        self.purposes = []
        self._draft_count = 0

    def chat(self, messages, tools=None):
        system = next(m["content"] for m in messages if m["role"] == "system")
        user = next((m["content"] or "") for m in messages if m["role"] == "user")
        purpose = self._purpose(system)
        self.purposes.append(purpose)
        return ChatResult(content=self._reply(purpose, system, user), usage=self.usage)

    @staticmethod
    def _purpose(system):
        for marker, name in (("证据提取器", "evidence"), ("素材整理器", "material"),
                             ("提纲规划器", "outline"), ("报告写作者", "draft"),
                             ("审校员", "review")):
            if marker in system:
                return name
        return "unknown"

    def _reply(self, purpose, system, user):
        if purpose == "evidence":
            return self._evidence(user)
        if purpose == "material":
            ids = self._ids(user)
            return json.dumps({"topics": [
                {"name": "主题甲", "points": [{"evidence_id": ids[i],
                                               "statement": f"主张{i}"}]}
                for i in range(min(2, len(ids)))] or [],
                "conflicts": [], "gaps": []}, ensure_ascii=False)
        if purpose == "outline":
            ids = self._ids(user)
            return json.dumps({"title": "桩报告", "sections": [
                {"heading": "背景", "purpose": "交代", "required_evidence": [ids[0]],
                 "require_fact_markers": True},
                {"heading": "结论", "purpose": "收束", "required_evidence": [ids[1]]}]},
                ensure_ascii=False)
        if purpose == "draft":
            return json.dumps({"report_markdown": self._draft(user)},
                              ensure_ascii=False)
        return json.dumps({"issues": [], "verdict": "accepted"})

    @staticmethod
    def _ids(user):
        return list(dict.fromkeys(re.findall(r"E-\d{3}", user)))

    def _draft(self, user):
        self._draft_count += 1
        ids = self._ids(user)
        outline = re.search(r"报告标题：([^\n]+)", user)
        title = outline.group(1).strip() if outline else "桩报告"
        if self.mode == "stubborn" or (self.mode == "missing_once" and self._draft_count == 1):
            omit = ids[-1] if len(ids) >= 2 else None
        else:
            omit = None
        lines = [f"# {title}", ""]
        for match in re.finditer(r"^([^：\n]+)：([^\n]*?)；必须覆盖证据 ([^；\n]+)(；需事实/推断标注)?",
                                 user, re.MULTILINE):
            heading, purpose, required_raw, markers = (match.group(1).strip(),
                                                       match.group(2),
                                                       match.group(3).strip(),
                                                       match.group(4))
            required = [x.strip() for x in required_raw.split(",") if x.strip()]
            lines.append(f"## {heading}")
            if purpose:
                lines.append(f"本节目的：{purpose}")
            for req in required:
                if req == omit:
                    continue
                mark = "〔事实〕" if markers else ""
                lines.append(f"相关支持见 [{req}]{mark}。")
            if markers:
                lines.append("另注〔推断〕边界。")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _evidence(user):
        start = user.find("---- 来源全文开始 ----")
        end = user.find("---- 来源全文结束 ----")
        body = user[start + len("---- 来源全文开始 ----"):end] if start >= 0 else user
        lines = [ln for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        items = []
        for line in lines[:3]:
            quote = line.strip()[:34]
            if len(quote) < 6:
                continue
            items.append({"fact": f"从材料可见：{quote}…", "tag": "F", "quote": quote})
        return json.dumps({"items": items}, ensure_ascii=False)


def _run_pipeline(tmp_path, mode="good", brain_override=None, **kwargs):
    job_dir = tmp_path / "job"
    store = _store_with(job_dir, TEXTS)
    brain = brain_override or PipelineBrain(mode=mode)
    return run_research_pipeline(llm=brain, job_dir=job_dir, store=store,
                                 goal="整理并写一份带引用的报告", **kwargs), brain, job_dir, store


def test_pipeline_full_chain_accepted(tmp_path):
    result, brain, job_dir, _ = _run_pipeline(tmp_path)
    assert result.draft_level == "accepted"
    assert result.termination_reason == "success"
    assert result.final_text.startswith("# 桩报告")
    assert result.total_citations >= 2 and result.unresolved_citations == 0
    assert result.revised_rounds == 0
    assert result.final_artifact_id == "report.v1"
    assert brain.purposes[0] == "evidence"
    assert "review" in brain.purposes
    artifacts = json.loads((job_dir / "artifacts.json").read_text(encoding="utf-8"))["artifacts"]
    kinds = {a["kind"] for a in artifacts}
    assert kinds >= {"material_pack", "outline", "report", "review"}
    assert (job_dir / "evidence.json").exists()
    assert (job_dir / "pipeline.json").exists()
    # 引用与证据一一对应
    items = json.loads((job_dir / "evidence.json").read_text(encoding="utf-8"))["items"]
    assert {c for c in re.findall(r"\[(E-\d{3})\]", result.final_text)} <= {i["evidence_id"] for i in items}
    assert len(items) >= 2


def test_pipeline_revises_once_until_clean(tmp_path):
    result, brain, job_dir, _ = _run_pipeline(tmp_path, mode="missing_once")
    assert result.draft_level == "accepted"
    assert result.revised_rounds == 1
    assert result.final_artifact_id == "report.v2"
    artifacts = json.loads((job_dir / "artifacts.json").read_text(encoding="utf-8"))["artifacts"]
    report_ids = [a["artifact_id"] for a in artifacts if a["kind"] == "report"]
    assert report_ids == ["report.v1", "report.v2"]
    # 旧稿不覆盖，报告版本均可读
    from src.harness.storage.artifacts import ArtifactStore
    store = ArtifactStore(job_dir)
    assert store.read("report.v1")["text"] != store.read("report.v2")["text"]


def test_pipeline_exhausts_revision_rounds_and_delivers_draft(tmp_path):
    result, brain, _, _ = _run_pipeline(tmp_path, mode="stubborn", max_revision_rounds=2)
    assert result.draft_level == "draft"
    assert result.termination_reason == "incomplete"
    assert result.revised_rounds == 2
    assert "不视为验收成功" in result.message or "待完善草稿" in result.message
    assert any(s["stage"] == "review" for s in result.stages)


def test_pipeline_budget_stop_saves_partial_and_marks_draft(tmp_path):
    job_dir = tmp_path / "job"
    store = _store_with(job_dir, TEXTS)
    ledger = JobLedger(job_dir.parent / "job_ledger", _request_like())
    with job_scope(ledger):
        result = run_research_pipeline(llm=PipelineBrain(), job_dir=job_dir, store=store,
                                       goal="整理并写作", max_revision_rounds=2)
    assert result.termination_reason == "budget_exceeded"
    assert result.draft_level == "draft"
    assert "预算" in result.message
    assert any(s["status"] == "budget_stopped" for s in result.stages)
    assert (job_dir / "pipeline.json").exists()


def _request_like():
    from src.application.request import TaskRequest
    return TaskRequest("整理并写作", max_calls=1)


def test_pipeline_revision_from_base_draft(tmp_path):
    """S5-04 单次改稿：给定原稿 → 修订稿 accepted 且产生实质变更。"""
    job_dir = tmp_path / "job"
    store = _store_with(job_dir, TEXTS)
    base = "试点40人[S:s01]；没有设置对照组，不能据此证明因果。"
    result = run_research_pipeline(llm=PipelineBrain(), job_dir=job_dir,
                                   store=SourceStore(job_dir),
                                   goal="压缩并保留依据", max_revision_rounds=2,
                                   initial_draft=base)
    assert result.draft_level == "accepted"
    assert result.final_text.strip() != base.strip()
    assert any(s["status"].startswith("completed_revision")
               or "原稿" in s.get("message", "") for s in result.stages)


def test_pipeline_revision_no_change_guard(tmp_path):
    """改稿不得原样返回：no_change 是 error，耗尽修订轮后交付 draft。"""
    job_dir = tmp_path / "job"
    _store_with(job_dir, TEXTS)
    base = ("试点共40人参与问卷，答卷者满意率为75%，平均工单处理时长从10小时"
            "降至8小时；没有设置对照组，不能据此证明因果，需注明局限。")
    seen = {"drafts": 0}

    class EchoBrain(PipelineBrain):
        def _draft(self, user):
            seen["drafts"] += 1
            return json.dumps({"report_markdown": base}, ensure_ascii=False)
    result = run_research_pipeline(llm=EchoBrain(), job_dir=job_dir,
                                   store=SourceStore(job_dir),
                                   goal="改稿", max_revision_rounds=2,
                                   initial_draft=base)
    assert result.draft_level == "draft"
    assert result.revised_rounds == 2 and seen["drafts"] == 3
    # no_change 错误进入最终审校问题清单
    assert any(s.get("issue_counts", {}).get("error")
               for s in result.stages)


def test_pipeline_no_sources_and_no_evidence(tmp_path):
    job_dir = tmp_path / "job"
    empty = SourceStore(job_dir)
    result = run_research_pipeline(llm=PipelineBrain(), job_dir=job_dir, store=empty,
                                   goal="研究", max_revision_rounds=2)
    assert result.draft_level == "draft" and result.termination_reason == "incomplete"
    assert "没有可用资料" in result.message

    class EmptyEvidenceBrain(PipelineBrain):
        def _evidence(self, user):
            return json.dumps({"items": []})
    store = _store_with(job_dir.parent / "job2", TEXTS)
    result = run_research_pipeline(llm=EmptyEvidenceBrain(), job_dir=job_dir.parent / "job2",
                                   store=store, goal="研究", max_revision_rounds=2)
    assert result.draft_level == "draft" and result.termination_reason == "incomplete"
    assert "没有任何可定位证据" in result.message
