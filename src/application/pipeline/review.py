# -*- coding: utf-8 -*-
"""
application/pipeline/review.py —— 双层审校与有限修订（S3-09/10/11）

第一层（程序，无模型调用）：引用标记可解析、提纲必需证据全覆盖、章节齐全、
任务硬约束（必需章节/禁语/关键事实，S6-05 对齐）、
要求区分事实/推断/未知的章节有标注；
第二层（模型）：支持关系、遗漏、矛盾、风格。
error 级问题存在 → needs_revision；修订最多 max_revision_rounds 轮，每轮保存
review.vN 与 report.v(N+1)（版本化，不覆盖旧稿）；轮次耗尽仍有 error 或关键缺口
→ 交付“待完善草稿”（draft_level=draft），不显示验收成功。
"""
from __future__ import annotations

import re

from src.application.pipeline.evidence import collect_citations
from src.application.pipeline.model import (HardRequirements, OutlineSection,
                                            ReviewIssue, StageError)
from src.application.pipeline.prompts import (build_review_messages,
                                              format_outline_requirements)
from src.harness.model_gateway import model_call
from src.harness.structured import extract_json

_FACT_MARKER = re.compile(r"〔(事实|推断|未知)〕")
_HEADING_PREFIX = re.compile(r"^(?:#+\s*)?(?:[0-9]+[.、)）]|[一二三四五六七八九十]+[、.)）]|"
                             r"[（(][0-9一二三四五六七八九十]+[)）])?\s*")
_PUNCT = re.compile(r"[\s:：,，。.、;；\-—_*`\"'“”‘’()（）\[\]【】]+")


def normalize_heading(text: str) -> str:
    """章节名归一：去 #、去编号前缀、去空白与常见标点，便于跨写法比对。"""
    value = _HEADING_PREFIX.sub("", (text or "").strip())
    return _PUNCT.sub("", value).lower()


def report_headings(report: str) -> list[str]:
    """正文 Markdown 标题行（归一后）。"""
    return [title for _, title, _ in heading_index(report)]


def heading_index(report: str) -> list[tuple[int, str, int]]:
    """[(行号, 归一标题, 层级)]——层级用于按节取正文（含子标题内容）。"""
    entries: list[tuple[int, str, int]] = []
    for index, line in enumerate((report or "").splitlines()):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        title = normalize_heading(re.sub(r"^#+\s*", "", stripped))
        if title:
            entries.append((index, title, level))
    return entries


def best_heading(report: str, name: str) -> tuple[int, str, int] | None:
    """选择与要求最匹配的标题：优先精确匹配，否则取最短的包含匹配。

    这样"三点摘要"不会命中文档大标题「…三点摘要与研究局限」，也不会因父标题
    抢位而导致本节正文取空（此前按行首匹配 + 遇标题即停，会把含子标题的章节
    正文判成空，从而误报"没有标注"）。
    """
    target = normalize_heading(name)
    if not target:
        return None
    headings = heading_index(report)
    exact = [entry for entry in headings if entry[1] == target]
    if exact:
        return exact[0]
    contains = [entry for entry in headings if target in entry[1]]
    if not contains:
        return None
    return min(contains, key=lambda entry: len(entry[1]))


def section_in_report(report: str, name: str) -> bool:
    """要求的章节名是否作为标题出现（归一后包含匹配，容忍编号与标点差异）。"""
    needle = normalize_heading(name)
    if not needle:
        return False
    return any(needle in heading for heading in report_headings(report) if heading)


def hard_requirement_issues(report: str, requirements: HardRequirements | None,
                            ) -> tuple[list[ReviewIssue], dict]:
    """任务硬约束的程序层复验（S6-05 对齐）：

    - 必需章节缺失 → error（阻塞，与程序层章节检查同一严重度）；
    - 禁语疑似命中 → warn（S8-C：关键词命中≠语义违规，程序层只提示；判定交评测/人工）；
    - 关键事实未逐字出现 → warn（记录但不阻塞；语义覆盖由评测/人工判定）。
    返回 (issues, stats)。
    """
    issues: list[ReviewIssue] = []
    stats = {"required_sections_total": 0, "required_section_hits": 0,
             "forbidden_total": 0, "forbidden_hits": 0,
             "fact_total": 0, "fact_hits": 0}
    if requirements is None:
        return issues, stats
    text = report or ""
    for name in requirements.required_sections:
        stats["required_sections_total"] += 1
        if section_in_report(text, name):
            stats["required_section_hits"] += 1
        else:
            issues.append(ReviewIssue(
                "error", "required_section",
                f"正文缺少任务要求的章节「{name}」（标题需逐字保留该名称）"))
    for claim in requirements.forbidden_claims:
        stats["forbidden_total"] += 1
        if claim and claim in text:
            stats["forbidden_hits"] += 1
            issues.append(ReviewIssue(
                "warn", "forbidden",
                f"正文疑似命中任务禁止的表述「{claim}」（程序层只按字面提示；"
                "是否构成语义违规由评测/人工判定，请自行复核语境）"))
    for fact in requirements.key_facts:
        stats["fact_total"] += 1
        if fact and fact in text:
            stats["fact_hits"] += 1
        else:
            issues.append(ReviewIssue(
                "warn", "fact",
                f"任务要求覆盖的关键事实未在正文中出现：「{fact}」（请核对并保留原文措辞）"))
    return issues, stats


def hard_requirement_stats(report: str, requirements: HardRequirements | None) -> dict:
    """只取读数（不含问题清单），写入 pipeline.json/job.json 供对照独立评测。"""
    return hard_requirement_issues(report, requirements)[1]


# ---- 交付等级自述封顶（Q3-01 第 1 批：该拒/该降级却交成品）---------------------
# 实测（Q2-01 冻结批次）：r07/r12（预期"无法完成"）与 r03/o13/r11（预期"草稿"）都被判成成品，
# 而报告正文自己就写着"本报告无法给出…""现有资料不足以批准采购…"。程序层不能只看
# "章节齐、引用可解析"就放行成品——交付等级不得高于报告的自述。
_INABILITY_HEADING = re.compile(r"无法完成|未能完成|不能完成|无法给出|无法提供|无法回答|无法检索")
_INABILITY_VERB = re.compile(r"无法|不能|不足以|未能")
_DELIVERABLE_OBJECT = re.compile(r"给出|提供|完成|得出|确认|回答|检索")
# 自述"整件事做不了"：仅在明确自我陈述或"目标/任务/核心"语境下触发，避免误伤普通局限说明
_UNABLE_SELF = re.compile(r"本报告(无法|不能|未能)(给出|提供|完成|得出|确认|回答)")
_UNABLE_GOAL = re.compile(r"无法(给出|提供|完成|得出|确认)[^。；\n]{0,15}(这一目标|该目标|本任务|核心|该问题)")
# 自述"证据不足以支撑所请求的决定/结论"→ 只能按草稿交付。
# 只认"正式决定"类动词（批准/通过/采购/上线/签署/立项）；**不含**建议类表述
# （不应/暂不/不建议 扩大、推行等）——实测 r04 的交付物就是"建议暂不扩大试点"（预期成品），
# 用建议动词封顶会误伤真实产出。
_INSUFFICIENT_DECISION = (
    re.compile(r"不足以(批准|通过|采购|上线|签署|立项)"),
    re.compile(r"(资料|材料|证据|信息|数据)[^。；\n]{0,8}不足以[^。；\n]{0,12}"
               r"(批准|支持批准|支撑批准|作出决定|得出结论)"),
    re.compile(r"(补齐|补充)[^。；\n]{0,20}(前|之前)[^。；\n]{0,10}(不予批准|不批准|无法批准)"),
)

# Q3-01 第 2 批（O-08）用到的模式：内部标识泄漏 / 对输入规模的错误断言
_INTERNAL_LEAK_PATTERNS = (
    (re.compile(r"src_[0-9a-f]{6,}"), "内部来源 id"),
    (re.compile(r"job_[0-9a-f]{8,}"), "内部任务 id"),
    (re.compile(r"\b(evidence_id|artifact_id|pipeline\.json|stage_(material|outline|draft))\b"),
     "内部字段名"),
    (re.compile(r"duplicates\s*字段"), "素材包内部字段"),
)
_SOURCE_COUNT_CLAIM = re.compile(
    r"(只提供了?\s*1\s*份|仅有?\s*一份|只有一份(来源|材料)|第二份(材料)?(缺失|未提供)"
    r"|实际只提供\s*1\s*份)")
# O-08 ①：冲突声称与"缺席型"证据的字面模式（程序只提示，不做语义判定）
_CONFLICT_CLAIM = re.compile(r"(?<![无有不])冲突|(?<![无有不])矛盾|(?<![无有不])分歧")
_ABSENCE_ENTRY = re.compile(
    r"未提供|未提及|未涉及|未包含|未见|缺失|没有提供|没有提及|并无")
# O-08 ⑤：改稿产出的修订说明字面模式
_REVISION_NOTE = re.compile(
    r"修订说明|变更说明|修改说明|改动说明|删除了|移除了|删去了|已删除|不再包含")


def delivery_cap(report: str, sections: list[OutlineSection] | None = None
                 ) -> tuple[str, str]:
    """按报告自述给出交付等级上限（accepted/draft/unable）与理由。

    - 任务要求了"无法完成"类章节，且该章节自述做不了 → unable；
    - 全文出现"本报告无法给出/完成…"或"无法…（这一目标/本任务/核心）" → unable；
    - 出现"资料不足以批准/不应批准/补齐前不…"等决策不足表述 → draft；
    - 其余（普通局限、不能归因、未控制变量等）不封顶，保持 accepted。
    只做字面自述识别，不做语义判定；命中的原句进理由，便于人工复核。
    """
    text = report or ""
    section_names = [getattr(s, "heading", "") for s in (sections or [])]
    for name in [n for n in section_names if _INABILITY_HEADING.search(n or "")]:
        body = _section_body(text, name) or ""
        if body and _INABILITY_VERB.search(body) and _DELIVERABLE_OBJECT.search(body):
            return "unable", f"任务要求的「{name}」章节自述无法给出核心交付内容"

    for pattern in (_UNABLE_SELF, _UNABLE_GOAL):
        match = pattern.search(text)
        if match:
            return "unable", f"报告自述无法交付核心内容：「{match.group(0)}」"

    for pattern in _INSUFFICIENT_DECISION:
        match = pattern.search(text)
        if match:
            return "draft", f"报告自述证据不足以支撑所请求的决定：「{match.group(0)}」"

    return "accepted", ""


def program_checks(report: str, evidence_ids: set[str],
                   sections: list[OutlineSection],
                   base_draft: str | None = None,
                   requirements: HardRequirements | None = None,
                   evidence_tags: dict[str, str] | None = None,
                   source_count: int | None = None,
                   evidence_texts: dict[str, str] | None = None) -> list[ReviewIssue]:
    """第一层：不做语义判断，全部是结构事实检查。

    evidence_tags：evidence_id → tag（F/I/U），检查〔事实〕标注与证据强度是否一致；
    source_count：本次任务的可用来源数，检查报告对输入规模的断言是否与实际不符；
    evidence_texts：evidence_id → 证据原文，供"自造冲突"程序信号检查（O-08 ①）。
    """
    issues: list[ReviewIssue] = []
    if base_draft is not None and report.strip() == base_draft.strip():
        issues.append(ReviewIssue(
            "error", "no_change",
            "改稿结果与原稿完全相同：必须按任务要求产生实质变更"))
    hard_issues, _ = hard_requirement_issues(report, requirements)
    issues.extend(hard_issues)
    issues.extend(_content_quality_checks(report, evidence_tags, source_count,
                                          evidence_texts=evidence_texts,
                                          base_draft=base_draft))
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


def _content_quality_checks(report: str, evidence_tags: dict[str, str] | None,
                            source_count: int | None,
                            evidence_texts: dict[str, str] | None = None,
                            base_draft: str | None = None) -> list[ReviewIssue]:
    """Q3-01 第 2/3 批（O-08）：可程序判定的内容质量信号。

    ① 内部标识泄漏：来源/任务 id、素材包字段名等内部产物不得出现在交付正文
       （error，封顶交付等级；根因已在提示词侧修掉，见 prompts.NO_INTERNAL_ID_RULE）；
    ② 〔事实〕标注与证据强度不一致：整句只引用了推断/未知证据却标成〔事实〕
       （warn，不封顶——20 例人工确认批次校准显示该口径没有区分度，见函数内注释）；
    ③ 对输入规模的断言与实际不符：如"只提供 1 份来源/第二份缺失"而实际有多份
       （error，封顶；20 例人工批次里唯一命中的 o08 人工判定就是 draft）；
    ④ 自造"开放冲突"信号（O-08 ①，warn 不封顶）：句内声称来源间冲突/矛盾/分歧，
       但仅引用不足 2 处证据，或所引证据原文是"未提供/缺失"类否定表述——
       20 例人工批次里 8 例的自造冲突即此模式（把"某来源未提供 X"当成冲突一方）。
       判定交人工/评测，程序只提示；
    ⑤ 改稿缺修订说明（O-08 ⑤，warn）：改稿任务（base_draft 存在）的产出不含任何
       修订/变更/删除说明表述——v04 类问题（任务要求说明删除了哪些结论却未落实）。
    只做字面匹配，不做语义判定。
    """
    issues: list[ReviewIssue] = []
    text = report or ""
    for pattern, label in _INTERNAL_LEAK_PATTERNS:
        match = pattern.search(text)
        if match:
            issues.append(ReviewIssue(
                "error", "internal_leak",
                f"交付正文出现内部标识「{match.group(0)}」（{label}）：必须删除后重写"))
    if evidence_tags:
        for sentence in re.split(r"[。；;\n]", text):
            if "〔事实〕" not in sentence:
                continue
            ids = collect_citations(sentence)
            if not ids:
                continue
            tags = [evidence_tags.get(i) for i in ids]
            if all(t and t != "F" for t in tags):
                # 只报 warn，不封顶交付等级：用 20 例人工确认批次校准的结果是
                # 该口径没有区分度——人工判 accept 的报告里 7/9 也命中（r06 高达 21 处），
                # 人工判 draft 的报告里 10/11 命中。根因是 F/I/U 由证据提取器按
                # 「资料是否直接支持」判定，与正文里「这句话是不是事实」不是同一件事。
                # 待办见 docs/OPTIMIZATION_BACKLOG.md O-08 ②。
                issues.append(ReviewIssue(
                    "warn", "fact_label",
                    f"整句仅引用推断/未知证据（{','.join(ids)}）却标注为〔事实〕："
                    "建议改为〔推断〕/〔未知〕（仅供参考，不影响交付等级）"))
    if source_count and source_count >= 2:
        match = _SOURCE_COUNT_CLAIM.search(text)
        if match:
            issues.append(ReviewIssue(
                "error", "source_count",
                f"报告称「{match.group(0)}」，但本次实际有 {source_count} 份来源："
                "与输入不符，必须按实际来源数改写"))
    # ④ 自造"开放冲突"信号（O-08 ①）：字面匹配，warn 不封顶，判定交人工/评测
    for sentence in re.split(r"[。；;\n]", text):
        if not _CONFLICT_CLAIM.search(sentence):
            continue
        ids = collect_citations(sentence)
        reasons: list[str] = []
        if len(set(ids)) < 2:
            reasons.append("冲突声明仅引用 "
                           f"{len(set(ids))} 处证据（<2）")
        if evidence_texts:
            absence = [i for i in set(ids)
                       if _ABSENCE_ENTRY.search(evidence_texts.get(i, ""))]
            if absence:
                reasons.append(
                    f"所引证据 {','.join(sorted(absence))} 原文属『未提供/缺失』类表述")
        if reasons:
            issues.append(ReviewIssue(
                "warn", "unfounded_conflict",
                f"疑似自造冲突：{('；'.join(reasons))}（O-08 ① 模式，"
                "请人工核实冲突是否真实存在；不影响交付等级）"))
    # ⑤ 改稿缺修订说明（O-08 ⑤）
    if base_draft and text.strip() != base_draft.strip() \
            and not _REVISION_NOTE.search(text):
        issues.append(ReviewIssue(
            "warn", "revision_change_note",
            "改稿产出未包含修订/变更/删除说明（任务要求说明改动与删除了哪些结论）："
            "建议补充修订说明（v04 类问题，供人工复核；不影响交付等级）"))
    return issues


def model_review(llm, goal: str, report: str, evidence_index: str,
                 sections: list[OutlineSection],
                 requirements_block: str = "") -> tuple[list[ReviewIssue], str]:
    """第二层。返回 (issues, verdict)；解析失败自动重试一次并要求只输出 JSON。"""
    requirements = format_outline_requirements(sections)
    issues: list[ReviewIssue] = []
    data = None
    raw = ""
    for attempt in (1, 2):
        messages = build_review_messages(goal, report, evidence_index, requirements,
                                         requirements_block=requirements_block)
        if attempt == 2:
            messages = messages[:1] + [{
                "role": "system",
                "content": "上一次输出无法解析为 JSON。这次只输出一个紧凑、完整、合法的"
                           "JSON 对象：issues 宁少勿多（最多8条），不要围栏与解释。"}] \
                + messages[1:]
        reply = model_call(llm, messages, purpose="review", role="reviewer")
        raw = reply.content or ""
        data = extract_json(raw)
        if data is not None:
            break
    if not data:
        raise StageError("review", "审校输出不是合法 JSON 对象"
                         + (f"；原始回复片段：{raw[:200]}" if raw else ""))
    for raw_issue in data.get("issues") or []:
        if not isinstance(raw_issue, dict):
            continue
        severity = ReviewIssue.validate_severity(raw_issue.get("severity"))
        message = (raw_issue.get("message") or "").strip()
        code = (raw_issue.get("code") or "style").strip()
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
    return section_in_report(report, heading)


def _section_body(report: str, heading: str) -> str:
    """取该节正文：从最匹配的标题开始，直到下一个同级或更高级标题（含子标题内容）。

    修复：此前"遇标题即停"会把只有子标题的章节正文取空（含子标题里的〔事实/推断/
    未知〕标注），导致误报"章节要求标注但正文没有标注"。
    """
    chosen = best_heading(report, heading)
    if chosen is None:
        return ""
    index, _, level = chosen
    lines = (report or "").splitlines()
    body: list[str] = []
    for line_index in range(index + 1, len(lines)):
        stripped = lines[line_index].strip()
        if stripped.startswith("#"):
            next_level = len(stripped) - len(stripped.lstrip("#"))
            if next_level <= level:
                break
        body.append(lines[line_index])
    return "\n".join(body)


def format_issues(issues: list[ReviewIssue], limit: int = 40) -> str:
    lines = [f"[{i.severity}:{i.code}] {i.message}" for i in issues[:limit]]
    if len(issues) > limit:
        lines.append(f"…另有 {len(issues) - limit} 条问题略")
    return "\n".join(lines)
