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
from src.application.pipeline.model import (HardRequirements, OutlineSection,
                                            ReviewIssue, StageError)
from src.application.pipeline.outline import run_outline_stage
from src.application.pipeline.review import (hard_requirement_issues,
                                             hard_requirement_stats, model_review,
                                             program_checks, section_in_report)
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
    sections, title, issues, cannot_answer = run_outline_stage(
        Brain(), "目标", "素材", {"E-001"})
    assert len(sections) == 1 and title == "报告"
    assert sections[0].required_evidence == ["E-001"]
    assert any(issue["code"] == "citation" for issue in issues)
    assert cannot_answer is None

    class EmptyBrain:
        model_name = "stub"
        run_mode = "mock"

        def chat(self, messages, tools=None):
            return ChatResult(content=json.dumps(
                {"sections": [{"heading": "", "required_evidence": []}]}))
    with pytest.raises(StageError):
        run_outline_stage(EmptyBrain(), "目标", "素材", {"E-001"})


def test_outline_stage_cannot_answer_honored_only_without_sections():
    """S8 unable 机制：证据完全无法支撑任务时，模型可声明 cannot_answer（须带原因与缺失）。"""
    class CannotAnswerBrain:
        model_name = "stub"
        run_mode = "mock"

        def chat(self, messages, tools=None):
            prompt = "\n".join(m["content"] or "" for m in messages)
            assert "cannot_answer" in prompt          # 提示词已声明该出口
            return ChatResult(content=json.dumps({"cannot_answer": {
                "reason": "资料不含竞品X任何报价信息",
                "missing": ["竞品X定价"]}}, ensure_ascii=False))

    sections, title, issues, cannot_answer = run_outline_stage(
        CannotAnswerBrain(), "报告竞品X定价", "素材", {"E-001"})
    assert sections == [] and cannot_answer == {
        "reason": "资料不含竞品X任何报价信息", "missing": ["竞品X定价"]}

    class BothBrain(CannotAnswerBrain):
        def chat(self, messages, tools=None):
            return ChatResult(content=json.dumps({
                "title": "报告",
                "sections": [{"heading": "背景", "required_evidence": ["E-001"]}],
                "cannot_answer": {"reason": "偷懒理由", "missing": ["x"]}},
                ensure_ascii=False))

    sections2, _, issues2, cannot_answer2 = run_outline_stage(
        BothBrain(), "目标", "素材", {"E-001"})
    assert len(sections2) == 1 and cannot_answer2 is None   # 有章节时以章节为准，防偷懒
    assert any(i["code"] == "cannot_answer" for i in issues2)

    class IncompleteBrain(CannotAnswerBrain):
        def chat(self, messages, tools=None):
            return ChatResult(content=json.dumps(
                {"cannot_answer": {"reason": "只有理由没有缺失清单"}}))

    with pytest.raises(StageError):
        run_outline_stage(IncompleteBrain(), "目标", "素材", {"E-001"})


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


def test_section_body_handles_subheadings_and_title_collision():
    """回归：文档大标题含章节名、标注写在子标题下时不得误报"没有标注"。

    真实批次 o02/o03 暴露：`_section_body` 遇标题即停 + 包含匹配命中大标题，
    使「三点摘要」「局限」正文被判空 → 程序层误报缺标注 → 误判 draft。
    """
    from src.application.pipeline.review import _section_body, best_heading
    report = ("# 星桥团队试点三点摘要与研究局限\n\n"
              "## 三点摘要\n\n### 试点规模\n\n试点共40人 [E-002]〔事实〕。\n\n"
              "## 局限\n\n没有设置对照组 [E-005]〔推断〕。\n")
    assert best_heading(report, "三点摘要")[1] == "三点摘要"   # 精确匹配优先于大标题
    assert best_heading(report, "不存在的章节") is None
    body = _section_body(report, "三点摘要")
    assert "〔事实〕" in body and "试点共40人" in body
    assert "局限" not in body                                  # 同级标题处停止
    assert "〔推断〕" in _section_body(report, "局限")
    section = OutlineSection("三点摘要", "摘要", require_fact_markers=True)
    assert program_checks(report, {"E-002", "E-005"}, [section]) == []


def test_delivery_cap_from_report_self_declaration():
    """Q3-01 第 1 批：交付等级不得高于报告自述（该拒/该降级却交成品的实测案例）。

    真实样本：r07「无法完成的部分」自述"无法给出…最新结论"却判成品；
    r12「本报告无法给出竞品X…单价」；r03「现有资料不足以批准采购」。
    """
    from src.application.pipeline.review import delivery_cap

    # ① 任务要求的"无法完成"章节自述做不了 → unable
    inability = OutlineSection("无法完成的部分")
    report_unable = ("# 报告\n\n## 无法完成的部分\n\n"
                     "本次核查的检索服务不可用，无法检索新增独立研究【E-001】〔事实〕；"
                     "因此无法给出最新结论【E-001】〔推断〕。\n\n"
                     "## 已有证据\n\n旧摘要覆盖120人【E-002】〔事实〕。\n")
    cap, reason = delivery_cap(report_unable, [inability, OutlineSection("已有证据")])
    assert cap == "unable" and "无法完成的部分" in reason

    # ② 全文自述"本报告无法给出…" → unable（无该类章节时同样生效）
    report_self = "# 报告\n\n本报告无法给出竞品X在2026年的坐席单价【E-001】〔事实〕。\n"
    assert delivery_cap(report_self, [])[0] == "unable"

    # ③ 自述证据不足/不应据此决策 → draft
    report_gap = ("# 报告\n\n## 结论\n\n现有资料不足以批准采购并处理内部敏感资料"
                  "【E-005】〔事实〕；补齐并复核前不批准采购〔推断〕。\n")
    cap, reason = delivery_cap(report_gap, [])
    assert cap == "draft" and "不足以" in reason

    # ④ 普通局限说明不得误伤（r02 型：相关关系/不能归因）
    report_normal = ("# 报告\n\n## 结论\n\n材料仅显示相关关系，不能得出远程办公提升效率的"
                     "因果结论〔推断〕【E-006】；未控制工单难度变化【E-013】〔事实〕。\n")
    assert delivery_cap(report_normal, [])[0] == "accepted"
    report_ok = "# 报告\n\n## 资料目录\n\n- 标题：试点记录【E-001】〔事实〕\n"
    assert delivery_cap(report_ok, [OutlineSection("资料目录")])[0] == "accepted"

    # ⑤ 建议类交付物不得误伤（真实误伤案例 r04：交付物本身就是"建议暂不扩大试点"）
    report_advice = ("# 报告\n\n## 建议\n\n现有数据不足以支持扩大试点【E-007】〔事实〕；"
                     "建议暂不扩大试点【E-016】〔推断〕；分析建议暂不扩大试点〔推断〕。\n"
                     "## 下一步采集\n\n扩大试点前宜补充基线与对照〔推断〕。\n")
    assert delivery_cap(report_advice, [OutlineSection("建议")])[0] == "accepted"


def test_pipeline_self_declared_inability_caps_delivery(tmp_path):
    """链内集成：写作者自述无法完成 → 交付等级被程序层封顶，不再判 accepted。"""

    class UnableBrain(PipelineBrain):
        def _reply(self, purpose, system, user):
            if purpose == "outline":
                return json.dumps({"title": "桩报告", "sections": [
                    {"heading": "无法完成的部分", "required_evidence": []},
                    {"heading": "已有证据", "required_evidence": []}]}, ensure_ascii=False)
            return super()._reply(purpose, system, user)

        def _draft(self, user):
            return ("# 桩报告\n\n## 无法完成的部分\n\n本次检索服务不可用，"
                    "无法给出该问题的最新结论【E-001】〔推断〕。\n\n"
                    "## 已有证据\n\n试点共40人【E-002】〔事实〕。\n")

    result, _, _, _ = _run_pipeline(tmp_path, brain_override=UnableBrain())
    assert result.draft_level == "unable"
    assert result.termination_reason == "incomplete"
    assert "自述" in result.message
    assert any(s["status"] == "self_declared_cap" for s in result.stages)


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
    # S8 unable 机制：零可用证据是确定性"无法完成"，不再降为草稿
    assert result.draft_level == "unable" and result.termination_reason == "unable"
    assert "没有任何可定位证据" in result.message


def test_pipeline_cannot_answer_yields_unable(tmp_path):
    """提纲阶段声明 cannot_answer（原因+缺失清单）→ 链交付 unable，不写编造报告。"""

    class CannotAnswerBrain(PipelineBrain):
        def _reply(self, purpose, system, user):
            if purpose == "outline":
                return json.dumps({"cannot_answer": {
                    "reason": "资料不含竞品X定价信息", "missing": ["竞品X定价"]}},
                    ensure_ascii=False)
            return super()._reply(purpose, system, user)

    result, _, job_dir, _ = _run_pipeline(tmp_path, brain_override=CannotAnswerBrain())
    assert result.draft_level == "unable" and result.termination_reason == "unable"
    assert "无法完成" in result.message
    assert "资料不含竞品X定价信息" in result.message and "竞品X定价" in result.message
    outlines = sorted((job_dir / "artifacts").glob("outline.*.md"))
    assert outlines                                        # 提纲产物保留（记录判定依据）


# ---- S6-05 对齐：任务硬约束（必需章节/禁语/关键事实）在链内程序层复验 ------------
REQ_SECTIONS = HardRequirements(required_sections=("资料目录", "覆盖范围"))
REQ_FULL = HardRequirements(required_sections=("资料目录", "覆盖范围"),
                            forbidden_claims=("全体参与者75%满意",),
                            key_facts=("试点共40人",))
REQ_OK_REPORT = ("# 报告\n\n## 一、资料目录\n\n- 材料一 [E-001]\n\n"
                 "## 2. 覆盖范围\n\n试点共40人参与 [E-001]。\n")


def test_hard_requirement_issues_and_stats():
    issues, stats = hard_requirement_issues(REQ_OK_REPORT, REQ_FULL)
    assert issues == []  # 编号前缀不误伤；关键事实逐字出现
    assert stats == {"required_sections_total": 2, "required_section_hits": 2,
                     "forbidden_total": 1, "forbidden_hits": 0,
                     "fact_total": 1, "fact_hits": 1}
    assert section_in_report(REQ_OK_REPORT, "资料目录")
    assert not section_in_report(REQ_OK_REPORT, "局限")

    bad = "# 报告\n\n## 其他\n\n全体参与者75%满意 [E-001]。\n"
    issues, stats = hard_requirement_issues(bad, REQ_FULL)
    errors = {i.code for i in issues if i.severity == "error"}
    warns = {i.code for i in issues if i.severity == "warn"}
    assert errors == {"required_section"}  # S8-C：必需章节缺失仍阻塞
    assert warns == {"fact", "forbidden"}  # 禁语只按字面提示疑似命中，关键事实缺失记 warn，均不阻塞
    assert stats["required_section_hits"] == 0 and stats["forbidden_hits"] == 1
    assert stats["fact_hits"] == 0
    # 没有硬要求时不产生任何问题与读数
    assert hard_requirement_issues("# 报告\n\n正文\n", None)[0] == []
    assert hard_requirement_stats(bad, None)["required_sections_total"] == 0


def test_outline_stage_appends_task_required_sections():
    seen: dict = {}

    class OutlineBrain:
        model_name = "stub"
        run_mode = "mock"

        def chat(self, messages, tools=None):
            seen["user"] = next(m["content"] for m in messages if m["role"] == "user")
            return ChatResult(content=json.dumps({"title": "报告", "sections": [
                {"heading": "背景", "required_evidence": ["E-001"]}]},
                ensure_ascii=False))

    sections, _, issues, _ = run_outline_stage(OutlineBrain(), "目标", "素材", {"E-001"},
                                               requirements=REQ_SECTIONS)
    headings = [s.heading for s in sections]
    assert headings == ["背景", "资料目录", "覆盖范围"]
    assert any(i["code"] == "required_section" for i in issues)
    # 硬要求写进了提示词（模型能看见，不是只在事后判错）
    assert "必须出现的章节" in seen["user"] and "资料目录" in seen["user"]

    class AlreadyThereBrain(OutlineBrain):
        def chat(self, messages, tools=None):
            return ChatResult(content=json.dumps({"title": "报告", "sections": [
                {"heading": "一、资料目录", "required_evidence": ["E-001"]},
                {"heading": "覆盖范围", "required_evidence": ["E-001"]}]},
                ensure_ascii=False))

    sections, _, issues, _ = run_outline_stage(AlreadyThereBrain(), "目标", "素材", {"E-001"},
                                               requirements=REQ_SECTIONS)
    assert [s.heading for s in sections] == ["一、资料目录", "覆盖范围"]  # 不重复补入
    assert not [i for i in issues if i["code"] == "required_section"]


def test_pipeline_accepts_when_hard_requirements_met(tmp_path):
    result, _, job_dir, _ = _run_pipeline(tmp_path, hard_requirements=REQ_SECTIONS)
    assert result.draft_level == "accepted"
    assert result.hard_checks["required_section_hits"] == 2
    assert "任务硬约束复验" in result.message
    snapshot = json.loads((job_dir / "pipeline.json").read_text(encoding="utf-8"))
    assert snapshot["hard_requirements"]["required_sections"] == ["资料目录", "覆盖范围"]
    assert snapshot["result"]["hard_checks"]["required_sections_total"] == 2


def test_pipeline_blocks_acceptance_when_required_section_missing(tmp_path):
    """模拟 o08：正文缺任务要求的章节 → 程序层判 error，耗尽修订后交付 draft。"""

    class DropRequiredSection(PipelineBrain):
        def _draft(self, user):
            report = super()._draft(user)
            kept, skip = [], False
            for line in report.splitlines():
                if line.startswith("#"):
                    skip = "覆盖范围" in line
                if not skip:
                    kept.append(line)
            return "\n".join(kept)

    result, _, job_dir, _ = _run_pipeline(tmp_path, brain_override=DropRequiredSection(),
                                          hard_requirements=REQ_SECTIONS)
    assert result.draft_level == "draft"
    assert result.termination_reason == "incomplete"
    assert result.revised_rounds == 2            # 已尝试两轮修订仍不达标
    assert result.hard_checks["required_section_hits"] == 1
    assert result.unresolved_citations == 0      # 阻塞来自硬要求，不是引用问题
    reviews = sorted((job_dir / "artifacts").glob("review.*.json"))
    payload = json.loads(reviews[-1].read_text(encoding="utf-8"))
    codes = {i["code"] for i in payload["issues"]}
    assert "required_section" in codes
    assert any("覆盖范围" in i["message"] for i in payload["issues"])


def test_pipeline_flags_forbidden_claim_without_blocking(tmp_path):
    """S8-C：禁语疑似命中只记 warn 与读数，程序层不自行阻塞验收；判定交评测/人工。"""

    class LeakForbidden(PipelineBrain):
        def _draft(self, user):
            return super()._draft(user) + "\n全体参与者75%满意。\n"

    requirements = HardRequirements(forbidden_claims=("全体参与者75%满意",),
                                    key_facts=("试点共40人",))
    result, _, job_dir, _ = _run_pipeline(tmp_path, brain_override=LeakForbidden(),
                                          hard_requirements=requirements)
    assert result.draft_level == "accepted"       # 疑似命中不再是阻塞问题
    assert result.hard_checks["forbidden_hits"] == 1  # 读数仍如实记录
    assert result.hard_checks["fact_hits"] == 0
    assert "禁语命中 1" in result.message         # 提示写入交付消息，供评测/人工复核
    reviews = sorted((job_dir / "artifacts").glob("review.*.json"))
    issues = json.loads(reviews[-1].read_text(encoding="utf-8"))["issues"]
    flagged = [i for i in issues if i["code"] == "forbidden"]
    assert flagged and flagged[0]["severity"] == "warn"   # 只提示疑似，不定性
    assert "疑似" in flagged[0]["message"]
    assert any(i["code"] == "fact" and i["severity"] == "warn" for i in issues)
