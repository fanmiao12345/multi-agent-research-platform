# -*- coding: utf-8 -*-
"""
application/pipeline/outline.py —— 提纲阶段（S3-06）

模型给出报告结构；程序校验章节标题非空、required_evidence 都存在，
并保留"区分事实/推断/未知"标注要求给审校程序层。
任务硬约束（S6-05 对齐）：任务给出的必需章节在提纲中缺失时由程序补入，
把"任务要求"变成提纲的一部分（模型自造结构不得替代任务要求）。
"""
from __future__ import annotations

from src.application.pipeline.model import (HardRequirements, OutlineSection,
                                            StageError)
from src.application.pipeline.prompts import build_outline_messages
from src.application.pipeline.review import normalize_heading
from src.harness.model_gateway import model_call
from src.harness.structured import extract_json


def run_outline_stage(llm, goal: str, material_block: str,
                      evidence_ids: set[str],
                      requirements: HardRequirements | None = None,
                      ) -> tuple[list[OutlineSection], str, list[dict], dict | None]:
    """返回 (sections, title, issues, cannot_answer)。

    cannot_answer 非 None 表示模型判定证据完全无法支撑任务目标（S8 unable 出口）：
    此时 sections 必为空；程序校验 reason/missing 非空，且"有章节则以章节为准"
    （防止用拒绝偷懒）。
    """
    requirements_block = requirements.prompt_block() if requirements else ""
    data = None
    raw = ""
    for attempt in (1, 2):
        messages = build_outline_messages(goal, material_block, requirements_block)
        if attempt == 2:
            messages = messages[:1] + [{
                "role": "system",
                "content": "上一次输出无法解析为 JSON。这次只输出一个紧凑、完整的 JSON"
                           "对象（sections 宁少勿多，最多8节），不要围栏与解释。"}] \
                + messages[1:]
        reply = model_call(llm, messages, purpose="outline", role="outline")
        raw = reply.content or ""
        data = extract_json(raw)
        if data is not None:
            break
    issues: list[dict] = []
    if not data:
        raise StageError("outline", "提纲输出不是合法 JSON 对象"
                         + (f"；原始回复片段：{raw[:200]}" if raw else ""))
    cannot_answer = None
    raw_ca = data.get("cannot_answer")
    if isinstance(raw_ca, dict):
        reason = str(raw_ca.get("reason") or "").strip()
        missing = [str(m).strip() for m in (raw_ca.get("missing") or [])
                   if str(m).strip()]
        if reason and missing:
            cannot_answer = {"reason": reason, "missing": missing}
        else:
            issues.append({"severity": "warn", "code": "cannot_answer",
                           "message": "cannot_answer 缺少 reason 或 missing，已忽略"})
    title = (data.get("title") or "").strip() or "未命名报告"
    sections: list[OutlineSection] = []
    for raw in data.get("sections") or []:
        if not isinstance(raw, dict):
            continue
        heading = (raw.get("heading") or "").strip()
        if not heading:
            issues.append({"severity": "warn", "code": "section",
                           "message": "提纲包含无标题章节，已丢弃"})
            continue
        required = []
        for token in raw.get("required_evidence") or []:
            token = str(token).strip()
            if token in evidence_ids:
                required.append(token)
            else:
                issues.append({"severity": "error", "code": "citation",
                               "message": f"提纲引用了不存在的证据 {token}（章节 {heading}）"})
        require_markers = bool(raw.get("require_fact_markers"))
        sections.append(OutlineSection(heading=heading,
                                       purpose=(raw.get("purpose") or "").strip(),
                                       required_evidence=required,
                                       require_fact_markers=require_markers))
    if not sections and cannot_answer is None:
        raise StageError("outline", "提纲没有任何有效章节")
    if cannot_answer and sections:
        # 防偷懒：能给出有效提纲就以章节为准，不接受同时拒绝
        cannot_answer = None
        issues.append({"severity": "warn", "code": "cannot_answer",
                       "message": "提纲同时给出章节与 cannot_answer，以章节为准继续写作"})
    if cannot_answer:
        return [], title, issues, cannot_answer
    if requirements is not None:
        existing = {normalize_heading(s.heading): s.heading for s in sections}
        for name in requirements.required_sections:
            needle = normalize_heading(name)
            if not needle or any(needle in key for key in existing if key):
                continue
            sections.append(OutlineSection(
                heading=name,
                purpose="任务硬性要求的章节（程序按任务要求补入，标题不得改写）"))
            issues.append({"severity": "warn", "code": "required_section",
                           "message": f"提纲缺少任务要求章节「{name}」，已按任务要求补入"})
    return sections, title, issues, None


def render_outline(title: str, sections: list[OutlineSection]) -> str:
    lines = [f"# {title}", ""]
    for index, section in enumerate(sections, start=1):
        lines.append(f"## {index}. {section.heading}")
        if section.purpose:
            lines.append(f"目的：{section.purpose}")
        if section.required_evidence:
            lines.append(f"必须覆盖证据：{', '.join(section.required_evidence)}")
        if section.require_fact_markers:
            lines.append("正文要求：区分〔事实〕/〔推断〕/〔未知〕")
        lines.append("")
    return "\n".join(lines)
