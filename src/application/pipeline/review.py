# -*- coding: utf-8 -*-
"""
application/pipeline/review.py —— 双层审校与有限修订（S3-09/10/11）

第一层（程序，无模型调用）：引用标记可解析、提纲必需证据全覆盖、章节齐全、
要求区分事实/推断/未知的章节有标注；
第二层（模型）：支持关系、遗漏、矛盾、风格。
error 级问题存在 → needs_revision；修订最多 max_revision_rounds 轮，每轮保存
review.vN 与 report.v(N+1)（版本化，不覆盖旧稿）；轮次耗尽仍有 error 或关键缺口
→ 交付“待完善草稿”（draft_level=draft），不显示验收成功。
"""
from __future__ import annotations

import re

from src.application.pipeline.evidence import collect_citations
from src.application.pipeline.model import OutlineSection, ReviewIssue, StageError
from src.application.pipeline.prompts import (build_review_messages,
                                              format_outline_requirements)
from src.harness.model_gateway import model_call
from src.harness.structured import extract_json

_FACT_MARKER = re.compile(r"〔(事实|推断|未知)〕")


def program_checks(report: str, evidence_ids: set[str],
                   sections: list[OutlineSection]) -> list[ReviewIssue]:
    """第一层：不做语义判断，全部是结构事实检查。"""
    issues: list[ReviewIssue] = []
    citations = collect_citations(report)
    unknown = sorted({c for c in citations if c not in evidence_ids})
    for token in unknown:
        issues.append(ReviewIssue("error", "citation",
                                  f"正文引用了证据库中不存在的证据 {token}"))
    cited = set(citations)
    for section in sections:
        for token in section.required_evidence:
            if token not in cited:
                issues.append(ReviewIssue(
                    "error", "coverage",
                    f"章节「{section.heading}」要求的证据 {token} 未被正文引用"))
        if not _heading_in_report(report, section.heading):
            issues.append(ReviewIssue("error", "section",
                                      f"正文缺少提纲章节「{section.heading}」"))
        if section.require_fact_markers:
            body = _section_body(report, section.heading)
            if not _FACT_MARKER.search(body):
                issues.append(ReviewIssue(
                    "error", "section",
                    f"章节「{section.heading}」要求区分事实/推断/未知，但正文没有标注"))
    if not citations:
        issues.append(ReviewIssue("warn", "citation", "正文没有任何引用标记，请确认每处断言都有证据"))
    return issues


def model_review(llm, goal: str, report: str, evidence_index: str,
                 sections: list[OutlineSection]) -> tuple[list[ReviewIssue], str]:
    """第二层。返回 (issues, verdict)。"""
    requirements = format_outline_requirements(sections)
    reply = model_call(llm, build_review_messages(goal, report, evidence_index, requirements),
                       purpose="review", role="reviewer")
    data = extract_json(reply.content or "")
    issues: list[ReviewIssue] = []
    if not data:
        raise StageError("review", "审校输出不是合法 JSON 对象")
    for raw in data.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        severity = ReviewIssue.validate_severity(raw.get("severity"))
        message = (raw.get("message") or "").strip()
        code = (raw.get("code") or "style").strip()
        if message:
            issues.append(ReviewIssue(severity, code, message))
    verdict = str(data.get("verdict") or "needs_revision").strip().lower()
    if verdict not in ("accepted", "needs_revision"):
        issues.append(ReviewIssue("warn", "format",
                                  f"审校 verdict 无法识别（{verdict!r}），按 needs_revision 处理"))
        verdict = "needs_revision"
    if any(i.severity == "error" for i in issues) and verdict == "accepted":
        issues.append(ReviewIssue("warn", "format",
                                  "审校含 error 却判 accepted，已改判 needs_revision"))
        verdict = "needs_revision"
    return issues, verdict


def _heading_in_report(report: str, heading: str) -> bool:
    needle = heading.strip().lower()
    for line in report.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = re.sub(r"^#+\s*", "", stripped).strip().lower()
            # 提纲编号前缀（如 "1. 背景"）在正文中可能省略序号
            if title == needle or title.endswith(" " + needle) or needle.endswith(" " + title):
                return True
    return False


def _section_body(report: str, heading: str) -> str:
    lines = report.splitlines()
    capture = False
    body: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            title = re.sub(r"^#+\s*", "", stripped).strip().lower()
            target = heading.strip().lower()
            hit = title == target or title.endswith(" " + target) or target.endswith(" " + title)
            if hit:
                capture = True
                continue
            if capture and stripped.startswith("#"):
                break
        elif capture:
            body.append(line)
    return "\n".join(body)


def format_issues(issues: list[ReviewIssue], limit: int = 40) -> str:
    lines = [f"[{i.severity}:{i.code}] {i.message}" for i in issues[:limit]]
    if len(issues) > limit:
        lines.append(f"…另有 {len(issues) - limit} 条问题略")
    return "\n".join(lines)
