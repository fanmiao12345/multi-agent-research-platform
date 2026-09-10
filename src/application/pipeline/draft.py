# -*- coding: utf-8 -*-
"""
application/pipeline/draft.py —— 初稿/修订写作（S3-06/02/10）

- 写作者收到提纲+素材包（按需还有上一稿与问题清单），直接产出 Markdown 正文；
- 引用必须使用提纲/素材中的 [E-编号]；章节标题与提纲一致（审校程序层复验）；
- 任务硬约束（必需章节/禁语/关键事实）随提示注入，正文由程序层复验（S6-05 对齐）；
- 修订通过 ArtifactStore 版本化保存：report.v1 →（问题）→ report.v2…，旧稿不覆盖。
"""
from __future__ import annotations

from src.application.pipeline.model import StageError
from src.application.pipeline.prompts import (build_draft_messages,
                                              format_material_block,
                                              format_outline_requirements)
from src.harness.model_gateway import model_call
from src.harness.structured import extract_json


def run_draft_stage(llm, goal: str, sections, title: str,
                    material_pack: dict, *,
                    previous_report: str = "", issues_block: str = "",
                    requirements_block: str = "") -> str:
    outline_block = format_outline_requirements(sections)
    if title:
        outline_block = f"报告标题：{title}\n\n" + outline_block
    messages = build_draft_messages(goal, outline_block,
                                    format_material_block(material_pack),
                                    previous_report=previous_report,
                                    revision_notes=issues_block,
                                    requirements_block=requirements_block)
    data = None
    raw = ""
    for attempt in (1, 2):
        if attempt == 2:
            messages = messages[:1] + [{
                "role": "system",
                "content": "上一次输出无法解析或缺少 report_markdown。这次只输出一个紧凑、"
                           "完整的 JSON：报告正文尽量 ≤1500 字，只包含提纲要求的章节，"
                           "不要围栏与解释。"}] + messages[1:]
        reply = model_call(llm, messages, purpose="draft", role="writer")
        raw = reply.content or ""
        data = extract_json(raw)
        if data and isinstance(data.get("report_markdown"), str) \
                and data["report_markdown"].strip():
            break
    if not data or not isinstance(data.get("report_markdown"), str) \
            or not data["report_markdown"].strip():
        raise StageError("draft", "初稿输出缺少 report_markdown 字段或为空"
                         + (f"；原始回复片段：{raw[:200]}" if raw else ""))
    report = data["report_markdown"].strip()
    if len(report) < 50:
        raise StageError("draft", "初稿过短，疑似未按提纲写作")
    return report
