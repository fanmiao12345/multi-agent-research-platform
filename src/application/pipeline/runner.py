# -*- coding: utf-8 -*-
"""
application/pipeline/runner.py —— 固定阶段的研究写作链执行器（S3-01/12/13 + S4-04/06/12）

- 阶段固定顺序：evidence → material → outline → draft → review（可带修订轮）；
- 产物先落盘（artifacts）→ 阶段检查点（stage_*.json）→ 外部状态提交（stage_hook，
  如 SQLite job 行）—— 崩溃窗口只可能是"文件在、状态未提交"，恢复由 resume 处理，
  绝不盲目全量重跑（S4-04）；
- resume=True：按检查点跳过已完成阶段并从检查点续跑（S4-12 业务阶段恢复边界）；
  默认对已有检查点的目录拒绝重跑，防止无意识覆盖；
- should_stop 每次阶段边界查询：收到取消请求后停止新调用、保存已有产物、
  以 cancelled 收敛（S4-06 的"已停止"）；链上模型调用全部走 model_call（预算传导）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from src.application.pipeline.draft import run_draft_stage
from src.application.pipeline.evidence import (EvidenceStore, collect_citations,
                                               extract_source_evidence, make_id)
from src.application.pipeline.material import (fill_duplicates, render_material,
                                               run_material_stage)
from src.application.pipeline.model import (HardRequirements, OutlineSection,
                                            PipelineResult, StageError)
from src.application.pipeline.outline import render_outline, run_outline_stage
from src.application.pipeline.review import (format_issues, hard_requirement_stats,
                                             model_review, program_checks)
from src.harness.model_gateway import BudgetStop
from src.harness.run_store import write_json
from src.harness.storage.artifacts import ArtifactStore

DEFAULT_MAX_REVISION_ROUNDS = 2
CP = {"material": "stage_material.json", "outline": "stage_outline.json",
      "draft": "stage_draft.json"}


class _StopRequest(Exception):
    """should_stop 触发：内部信号，收敛为 cancelled。"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _cp_path(job_dir: Path, name: str) -> Path:
    return job_dir / CP[name]


def _has_checkpoints(job_dir: Path) -> bool:
    return any((job_dir / name).exists() for name in CP.values())


def _load_cp(job_dir: Path, name: str) -> dict | None:
    path = _cp_path(job_dir, name)
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        raise StageError(name, f"检查点 {path.name} 损坏：拒绝静默跳过该阶段") from None


def _latest_artifact_text(artifacts: ArtifactStore, kind: str) -> str | None:
    versions = [a for a in artifacts.list() if a.get("kind") == kind]
    if not versions:
        return None
    latest = sorted(versions, key=lambda a: a.get("version") or 0)[-1]
    try:
        return artifacts.read(latest["artifact_id"]).get("text", "")
    except Exception:
        return None


def run_research_pipeline(*, llm, job_dir: Path, store, goal: str,
                          max_revision_rounds: int = DEFAULT_MAX_REVISION_ROUNDS,
                          on_progress=None, resume: bool = False,
                          should_stop=None, stage_hook=None,
                          initial_draft: str | None = None,
                          hard_requirements: HardRequirements | None = None,
                          delivery_kind: str = "auto", repair_callback=None) -> PipelineResult:
    """执行整条链并返回结果；阶段快照写 pipeline.json。

    initial_draft：改稿模式（S5-04 单次改稿）——以给定原稿为上一稿，
    素材/提纲照常基于资料生成，写作者按任务要求改写；程序层额外检查
    "必须产生实质变更"（原样返回视为 error，进修订轮）。

    hard_requirements：任务硬约束（必需章节/禁语/关键事实，S6-05 对齐）——
    注入提纲与初稿提示词，并在程序层逐条复验；必需章节缺失或禁语出现是阻塞
    error，不能判 accepted（缺章交付 draft）。缺省 None 表示无额外硬要求。

    stage_hook(stage, status, artifact_ids, message)：产物与检查点落盘后回调，
    供外部（SQLite job 行等）提交状态 —— 顺序保证"文件先、状态后"。
    """
    def infer_delivery_kind() -> str:
        if delivery_kind in ("collection", "analysis", "report"):
            return delivery_kind
        if hard_requirements and hard_requirements.required_sections:
            return "report"
        low = (goal or "").lower()
        if any(word in low for word in ("报告", "写作", "成稿", "论文", "初稿")):
            return "report"
        if any(word in low for word in ("整理", "目录", "清单", "分类", "归纳")):
            return "collection"
        if any(word in low for word in ("分析", "研判", "评估", "比较", "结论")):
            return "analysis"
        return "report"

    result = PipelineResult(delivery_kind=infer_delivery_kind())
    requirements = hard_requirements if (hard_requirements and not hard_requirements.is_empty) \
        else None
    requirements_block = requirements.prompt_block() if requirements else ""
    artifacts = ArtifactStore(job_dir)
    evidence_store = EvidenceStore(job_dir)
    started = _now()
    stop_query = should_stop or (lambda: False)
    hook = stage_hook or (lambda *a, **k: None)

    def progress(stage: str, message: str):
        if on_progress:
            try:
                on_progress({"type": "stage", "stage": stage, "message": message})
            except Exception:  # noqa: BLE001 —— 进度回调失败不阻断链
                pass

    def boundary(stage: str):
        """阶段边界：检查取消请求（S4-06：停止新调用）。"""
        try:
            if stop_query():
                raise _StopRequest()
        except _StopRequest:
            raise
        except Exception as e:  # noqa: BLE001 —— 查询失败按未取消处理
            progress(stage, f"取消检查失败：{type(e).__name__}")

    def record_stage(stage: str, status: str, artifact_ids: list[str] | None = None,
                     issues: list | None = None, message: str = ""):
        entry = {"stage": stage, "status": status,
                 "artifact_ids": artifact_ids or [],
                 "issue_counts": _count_issues(issues or []),
                 "message": message, "finished_at": _now()}
        result.stages.append(entry)
        hook(stage, status, artifact_ids or [], message)

    def snapshot():
        write_json(job_dir / "pipeline.json",
                   {"schema_version": 1, "root_job_id": job_dir.name,
                    "goal": goal, "started_at": started,
                    "hard_requirements": requirements.as_dict() if requirements else {},
                    "result": result.as_dict()})

    def cancel_outcome(stage: str, message: str):
        result.draft_level = "draft"
        result.termination_reason = "cancelled"
        result.message = message
        record_stage(stage, "stopped", message=message)
        snapshot()

    try:
        boundary("start")
        if not resume and _has_checkpoints(job_dir):
            raise StageError(
                "chain", f"{job_dir} 已存在阶段检查点：拒绝无意识重跑，"
                         "请显式 resume 或使用新任务目录")
        index = store.summary().get("sources", [])
        usable = [r for r in index if r["status"] in ("ok", "partial") and r.get("file_name")]
        if not usable:
            result.draft_level = "draft"
            result.termination_reason = "incomplete"
            result.message = ("没有可用资料：研究写作需要至少一份可用来源"
                              "（搜索未配置时只能使用用户提供的资料/链接）")
            record_stage("evidence", "no_sources", message=result.message)
            snapshot()
            return result

        # ---- 证据提取（S3-04，逐来源；检查点=evidence.json）-------------
        existing_items = evidence_store.load_all() if resume else []
        if resume and existing_items:
            all_items = existing_items
            all_issues = []
            record_stage("evidence", "resumed",
                         message=f"复用 {len(all_items)} 条已提取证据（不重复抽取）")
        else:
            all_items = []
            all_issues = []
            sequence = 0
            for pos, record in enumerate(usable, start=1):
                boundary("evidence")
                progress("evidence",
                         f"正在提取证据 {pos}/{len(usable)}：{record['display']}")
                text = store.full_text(record["source_id"]) or ""
                items, issues = extract_source_evidence(
                    llm, goal, {"source_id": record["source_id"],
                                "title": record.get("title", ""),
                                "display": record.get("display", record["source_id"]),
                                "text": text,
                                "segments": store.segments(record["source_id"])})
                all_issues.extend(issues)
                for item in items:
                    sequence += 1
                    item.evidence_id = make_id(sequence)
                    all_items.append(item)
            evidence_store.save_all(all_items)
            if not all_items:
                # S8 unable 机制：零可用证据是确定性"无法完成"，不写编造报告
                result.draft_level = "unable"
                result.termination_reason = "unable"
                result.message = "证据提取后没有任何可定位证据，无法继续写作（详见问题记录）"
                record_stage("evidence", "no_evidence", issues=all_issues,
                             message=result.message)
                snapshot()
                return result
            record_stage("evidence", "completed", issues=all_issues,
                         message=f"提取 {len(all_items)} 条可定位证据，跳过 "
                                 f"{len(all_issues)} 条问题摘录")
        evidence_items = [item if isinstance(item, dict) else item.as_dict()
                          for item in all_items]

        # ---- 素材包（S3-05）---------------------------------------------
        boundary("material")
        if resume and _load_cp(job_dir, "material"):
            pack_cp = _load_cp(job_dir, "material")
            from src.application.pipeline.model import MaterialPack
            pack = MaterialPack(topics=pack_cp["pack"]["topics"],
                                conflicts=pack_cp["pack"]["conflicts"],
                                gaps=pack_cp["pack"]["gaps"],
                                duplicates=pack_cp["pack"]["duplicates"])
            pack_artifact = None
            record_stage("material", "resumed",
                         message=f"复用素材包（{len(pack.topics)} 个主题）")
        else:
            progress("material", "正在整理素材包")
            pack, pack_issues = run_material_stage(llm, goal, evidence_items)
            fill_duplicates(pack, index)
            artifact = artifacts.save("material_pack", render_material(pack),
                                      producer="pipeline-material")
            write_json(_cp_path(job_dir, "material"),
                       {"pack": pack.as_dict()})
            record_stage("material", "completed", [artifact["artifact_id"]],
                         issues=pack_issues,
                         message=f"{len(pack.topics)} 个主题，"
                                 f"{len(pack.conflicts)} 个开放冲突，"
                                 f"{len(pack.gaps)} 个缺口")
            pack_artifact = artifact

        # ---- D7-03：一次有界补做（明确缺口 -> 新来源 -> 更新素材）---------
        repair_result = None
        if pack.gaps and repair_callback is not None:
            boundary("repair")
            try:
                repaired = repair_callback([dict(g) for g in pack.gaps])
                if isinstance(repaired, str):
                    repaired = [repaired]
                repaired = [str(text).strip() for text in (repaired or []) if str(text).strip()]
                added_evidence = []
                for text in repaired:
                    display_index = len(store.summary()["sources"]) + 1
                    record = store.add_paste(text, display_index=display_index)
                    if record.status not in ("ok", "partial"):
                        continue
                    items, issues = extract_source_evidence(
                        llm, goal, {"source_id": record.source_id,
                                    "title": record.title, "display": record.display,
                                    "text": store.full_text(record.source_id) or "",
                                    "segments": store.segments(record.source_id)})
                    all_issues.extend(issues)
                    for item in items:
                        sequence += 1
                        item.evidence_id = make_id(sequence)
                        all_items.append(item)
                        added_evidence.append(item)
                if added_evidence:
                    evidence_store.save_all(all_items)
                    evidence_items = [item if isinstance(item, dict) else item.as_dict()
                                      for item in all_items]
                    pack, pack_issues = run_material_stage(llm, goal, evidence_items)
                    fill_duplicates(pack, store.summary()["sources"])
                    artifact = artifacts.save("material_pack", render_material(pack),
                                              producer="pipeline-repair")
                    pack_artifact = artifact
                    repair_result = {"added_sources": len(repaired),
                                     "added_evidence": len(added_evidence),
                                     "remaining_gaps": len(pack.gaps)}
                    record_stage("repair", "completed", [artifact["artifact_id"]],
                                 issues=pack_issues, message=str(repair_result))
                else:
                    repair_result = {"added_sources": len(repaired), "added_evidence": 0}
                    record_stage("repair", "no_new_evidence", issues=all_issues,
                                 message="补做未产生可定位新证据")
            except BudgetStop:
                raise
            except Exception as e:  # noqa: BLE001
                repair_result = {"error": f"{type(e).__name__}: {e}"}
                record_stage("repair", "failed", message=repair_result["error"])

        if repair_result is not None:
            result.message = (result.message + "；" if result.message else "") + f"补做：{repair_result}"

        # ---- D7-02：短交付路径（整理/分析）-------------------------------
        if result.delivery_kind == "collection":
            final_text = render_material(pack)
            artifact = artifacts.save("collection", final_text,
                                      producer="pipeline-collection")
            result.final_artifact_id = artifact["artifact_id"]
            result.final_text = final_text
            result.draft_level = "accepted" if evidence_items else "draft"
            result.termination_reason = "success" if evidence_items else "incomplete"
            result.message = "已按整理清单交付可追溯素材、冲突与缺口"
            result.total_citations = len(collect_citations(final_text))
            result.unresolved_citations = len(
                {c for c in collect_citations(final_text)
                 if c not in evidence_store.ids()})
            record_stage("delivery", "completed", [artifact["artifact_id"]],
                         message=result.message)
            snapshot()
            return result
        if result.delivery_kind == "analysis":
            evidence_ids = list(evidence_store.ids())
            sections = [
                OutlineSection("核心结论", purpose="直接回答任务目标",
                               required_evidence=evidence_ids[:8],
                               require_fact_markers=True),
                OutlineSection("依据与限制", purpose="列出来源、推断和未知",
                               required_evidence=evidence_ids[:8]),
            ]
            for name in (requirements.required_sections if requirements else ()):
                if all(name != section.heading for section in sections):
                    sections.append(OutlineSection(name))
            analysis = run_draft_stage(
                llm, goal, sections, "分析", pack.as_dict(),
                requirements_block=requirements_block)
            artifact = artifacts.save("analysis", analysis,
                                      producer="pipeline-analysis")
            program = program_checks(analysis, evidence_ids, sections,
                                     requirements=requirements)
            errors = [issue for issue in program if issue.severity == "error"]
            result.final_artifact_id = artifact["artifact_id"]
            result.final_text = analysis
            result.hard_checks = hard_requirement_stats(analysis, requirements)
            result.total_citations = len(collect_citations(analysis))
            result.unresolved_citations = len(
                {c for c in collect_citations(analysis) if c not in evidence_ids})
            result.draft_level = "accepted" if not errors else "draft"
            result.termination_reason = "success" if not errors else "incomplete"
            result.message = ("分析交付完成" if not errors else
                              "已交付待完善分析，仍有引用或覆盖问题")
            record_stage("delivery", "completed" if not errors else "needs_revision",
                         [artifact["artifact_id"]], issues=[i.as_dict() for i in program],
                         message=result.message)
            snapshot()
            return result

        # ---- 提纲（S3-06）-----------------------------------------------
        boundary("outline")
        cannot_answer = None
        if resume and _load_cp(job_dir, "outline"):
            outline_cp = _load_cp(job_dir, "outline")
            sections = [OutlineSection(**{k: s[k] for k in
                                          ("heading", "purpose",
                                           "required_evidence",
                                           "require_fact_markers")})
                        for s in outline_cp["sections"]]
            title = outline_cp["title"]
            cannot_answer = outline_cp.get("cannot_answer") or None
            record_stage("outline", "resumed",
                         message=f"复用提纲（{len(sections)} 个章节）")
        else:
            progress("outline", "正在生成提纲")
            sections, title, outline_issues, cannot_answer = run_outline_stage(
                llm, goal, render_material(pack), evidence_store.ids(),
                requirements=requirements)
            artifact = artifacts.save("outline", render_outline(title, sections),
                                      producer="pipeline-outline")
            write_json(_cp_path(job_dir, "outline"),
                       {"title": title,
                        "sections": [s.as_dict() for s in sections],
                        "cannot_answer": cannot_answer})
            record_stage("outline", "completed", [artifact["artifact_id"]],
                         issues=outline_issues,
                         message=(f"无法完成：{cannot_answer['reason']}" if cannot_answer
                                  else f"{len(sections)} 个章节"))
        if cannot_answer and not sections:
            # S8 unable 机制：证据完全无法支撑任务目标 → 主动交付"无法完成"
            missing = "、".join(cannot_answer.get("missing") or [])
            result.draft_level = "unable"
            result.termination_reason = "unable"
            result.final_text = ""
            result.message = (f"无法完成：{cannot_answer.get('reason', '')}"
                              + (f"；缺失信息：{missing}" if missing else "")
                              + f"（可用证据 {len(evidence_store.ids())} 条不足以支撑"
                                "任务目标，按任务要求不编造内容）")
            snapshot()
            return result

        # ---- 初稿 + 双层审校 + 有限修订（S3-09/10；改稿见 S5-04）------------
        boundary("draft")
        base = (initial_draft or "").strip()
        if resume and _load_cp(job_dir, "draft"):
            report = _load_cp(job_dir, "draft")["text"]
            resume_artifact = _latest_artifact(artifacts, "report")
            if resume_artifact is not None:
                result.final_artifact_id = resume_artifact["artifact_id"]
            record_stage("draft", "resumed", message="复用已保存的初稿")
        else:
            progress("draft", "正在写作初稿")
            report = run_draft_stage(llm, goal, sections, title,
                                     pack.as_dict(),
                                     previous_report=base or "",
                                     issues_block=("请按任务要求修改这份原稿，"
                                                   "保留可用引用并修正/删除不再受支持的表述。"
                                                   if base else ""),
                                     requirements_block=requirements_block)
            artifact = artifacts.save("report", report,
                                      producer="pipeline-writer")
            write_json(_cp_path(job_dir, "draft"), {"text": report})
            record_stage("draft", "completed" + ("_revision" if base else ""),
                         [artifact["artifact_id"]],
                         message=f"{'基于原稿改稿' if base else '新写初稿'}（{len(report)}字）")
            result.final_artifact_id = artifact["artifact_id"]

        verdict = "needs_revision"
        final_issues: list = []
        for round_index in range(max_revision_rounds + 1):
            boundary("review")
            progress("review", f"双层审校 第 {round_index + 1} 轮")
            program = program_checks(report, evidence_store.ids(), sections,
                                     base_draft=base or None,
                                     requirements=requirements)
            evidence_index = "\n".join(
                f"- {item['evidence_id']} {item['fact']}"
                f"（来源 {item['source_id']}）" for item in evidence_items)
            model_issues, verdict = model_review(llm, goal, report,
                                                 evidence_index, sections,
                                                 requirements_block=requirements_block)
            issues = program + model_issues
            final_issues = issues
            review_payload = {"schema_version": 1, "round": round_index + 1,
                              "verdict": verdict,
                              "issues": [i.as_dict() for i in issues]}
            review_artifact = artifacts.save(
                "review", json.dumps(review_payload, ensure_ascii=False, indent=1),
                ext="json", producer="pipeline-review")
            result.revised_rounds = round_index
            errors = [i for i in issues if i.severity == "error"]
            if not errors and verdict == "accepted":
                break
            if round_index >= max_revision_rounds:
                break
            progress("draft", f"按审校意见修订（第 {round_index + 1} 轮）")
            report = run_draft_stage(llm, goal, sections, title,
                                     pack.as_dict(),
                                     previous_report=report,
                                     issues_block=format_issues(issues),
                                     requirements_block=requirements_block)
            draft_artifact = artifacts.save("report", report,
                                            producer="pipeline-writer",
                                            parent_version=_version_of(
                                                result.final_artifact_id))
            write_json(_cp_path(job_dir, "draft"), {"text": report})
            result.final_artifact_id = draft_artifact["artifact_id"]
            record_stage("review", "revised",
                         [review_artifact["artifact_id"],
                          draft_artifact["artifact_id"]],
                         issues=issues,
                         message=f"第 {round_index + 1} 轮修订后重审")
        errors = [i for i in final_issues if i.severity == "error"]
        result.final_text = report
        result.hard_checks = hard_requirement_stats(report, requirements)
        citations = collect_citations(report)
        result.total_citations = len(citations)
        result.unresolved_citations = len(
            {c for c in citations if c not in evidence_store.ids()})
        if not errors and verdict == "accepted":
            result.draft_level = "accepted"
            result.termination_reason = "success"
            result.message = "双层审校通过（引用可定位、章节齐全、无阻塞问题）"
            if requirements:
                stats = result.hard_checks
                result.message += (
                    "；任务硬约束复验：必需章节 "
                    f"{stats['required_section_hits']}/{stats['required_sections_total']}、"
                    f"禁语命中 {stats['forbidden_hits']}、"
                    f"关键事实 {stats['fact_hits']}/{stats['fact_total']}")
            record_stage("review", "accepted",
                         artifact_ids=[result.final_artifact_id or ""],
                         issues=final_issues, message=result.message)
        else:
            result.draft_level = "draft"
            result.termination_reason = "incomplete"
            result.message = (f"交付待完善草稿：仍有 {len(errors)} 个阻塞问题或审校未通过"
                              "（详见 review 产物），不视为验收成功")
            record_stage("review", "needs_revision", issues=final_issues,
                         message=result.message)
        snapshot()
        return result
    except BudgetStop as e:
        result.draft_level = "draft"
        result.termination_reason = "budget_exceeded"
        result.message = f"根预算已停止（{e}）：保留已保存的阶段产物，不视为验收成功"
        record_stage("current", "budget_stopped", message=str(e))
        snapshot()
        return result
    except _StopRequest:
        cancel_outcome("current", "收到取消请求：已停止新调用并保留已保存产物")
        return result
    except StageError as e:
        result.draft_level = "failed"
        result.termination_reason = "error"
        result.message = f"阶段 {e.stage} 失败：{e}"
        record_stage(e.stage, "failed", message=str(e))
        snapshot()
        return result


def _latest_artifact(artifacts: ArtifactStore, kind: str) -> dict | None:
    versions = [a for a in artifacts.list() if a.get("kind") == kind]
    if not versions:
        return None
    return sorted(versions, key=lambda a: a.get("version") or 0)[-1]


def _count_issues(issues: list) -> dict:
    counts = {"error": 0, "warn": 0, "info": 0}
    for issue in issues:
        severity = issue.severity if hasattr(issue, "severity") else issue.get("severity")
        if severity in counts:
            counts[severity] += 1
    return counts


def _version_of(artifact_id: str) -> int:
    try:
        return int(artifact_id.rsplit(".v", 1)[1])
    except (IndexError, ValueError):
        return 1
