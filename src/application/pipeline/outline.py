# -*- coding: utf-8 -*-
"""
application/pipeline/outline.py —— 提纲阶段（S3-06）

模型给出报告结构；程序校验章节标题非空、required_evidence 都存在，
并保留"区分事实/推断/未知"标注要求给审校程序层。
"""
from __future__ import annotations

from src.application.pipeline.model import OutlineSection, StageError
from src.application.pipeline.prompts import build_outline_messages
from src.harness.model_gateway import model_call
from src.harness.structured import extract_json


def run_outline_stage(llm, goal: str, material_block: str,
                      evidence_ids: set[str]) -> tuple[list[OutlineSection], str, list[dict]]:
    data = None
    raw = ""
    for attempt in (1, 2):
        messages = build_outline_messages(goal, material_block)
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
    if not sections:
        raise StageError("outline", "提纲没有任何有效章节")
    return sections, title, issues


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
