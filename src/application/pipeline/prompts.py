# -*- coding: utf-8 -*-
"""
application/pipeline/prompts.py —— 各阶段提示词模板（集中管理、教学化）

统一红线（写进每个阶段）：
- 只允许使用提供的资料与证据；不得引用资料之外的事实；
- 不得编造 evidence_id / 来源；quote 必须与原文逐字一致；
- 结构化输出：JSON（除初稿含正文外均机器校验，失败按阶段错误处理）。
"""
from __future__ import annotations

import json

GOAL_RULES = ("你正在完成一份可核查的资料整理/研究写作任务。"
              "只允许依据给定的资料与证据。禁止引用资料之外的事实，"
              "禁止编造来源；做不到的内容如实写进缺口，不能假装完成。")

JSON_RULE = ("只输出一个合法 JSON 对象：紧凑单行（不要格式化缩进、不要空行），"
             "不要 ```json 围栏，不要任何解释。")


def _s(content: str) -> dict:
    return {"role": "system", "content": content}


def _u(content: str) -> dict:
    return {"role": "user", "content": content}


# ---- 证据提取（S3-04） --------------------------------------------------
def build_evidence_messages(goal: str, source: dict) -> list[dict]:
    """source: {source_id,title,display,text(预算内),truncated:bool}"""
    note = ("（注意：该来源过长，只提供了前段文本；不得猜测未提供的后段内容，"
            "如需可如实写入缺口。）" if source.get("truncated") else "")
    schema = ('{"items":[{"fact":"支持的事实/主张，一句话≤60字","tag":"F|I|U",'
              '"quote":"原文连续片段，必须与资料逐字一致，≤80字符"}]}')
    return [
        _s(GOAL_RULES + " 你是证据提取器。给出来源中与本任务相关的事实主张；"
           "tag：F=资料直接支持的事实，I=有依据的推断，U=资料未说明、只是你的猜测（尽量少）。"
           f"【数量与长度】items 最多 8 条；fact 一句话≤60字；quote ≤80字符；"
           f"不要输出示例之外的任何内容。{JSON_RULE} 输出结构：{schema}"),
        _u(f"任务目标：{goal}\n\n来源：{source['display']}（id={source['source_id']}）"
           f"\n标题：{source.get('title') or '（无标题）'}{note}\n\n"
           f"---- 来源全文开始 ----\n{source['text']}\n---- 来源全文结束 ----"),
    ]


# ---- 素材包（S3-05） ----------------------------------------------------
def build_material_messages(goal: str, evidence_block: str) -> list[dict]:
    schema = ('{"topics":[{"name":"主题名","points":[{"evidence_id":"E-001",'
              '"statement":"该证据支撑的一句话"}]}],'
              '"conflicts":[{"statement":"互相矛盾的主张","evidence_ids":["E-001","E-002"],'
              '"status":"open"}],'
              '"gaps":[{"question":"待补问题","missing":"缺少哪种材料"}]}')
    return [
        _s(GOAL_RULES + " 你是素材整理器。按主题组织证据；可以指出冲突与缺口，"
           "但【禁止】无依据地消解矛盾：冲突的 status 只能是 open。"
           "evidence_id 只能使用给定列表中的。"
           "【数量】topics≤10、每主题 points≤8、conflicts≤5、gaps≤5，statement 一句话≤60字。"
           + JSON_RULE + f' 输出结构：{schema}'),
        _u(f"任务目标：{goal}\n\n已提取证据列表（evidence_id/fact/tag/来源）：\n"
           f"{evidence_block}\n\n请输出素材包 JSON。"),
    ]


# ---- 提纲（S3-06） ------------------------------------------------------
def build_outline_messages(goal: str, material_block: str) -> list[dict]:
    schema = ('{"title":"报告标题","sections":[{"heading":"章节标题",'
              '"purpose":"本节要回答什么","required_evidence":["E-001"],'
              '"require_fact_markers":true}]}')
    return [
        _s(GOAL_RULES + " 你是提纲规划器。根据素材包设计固定顺序的报告提纲；"
           "每个章节给出必须覆盖的证据 id 与是否要求正文标注事实/推断/未知。"
           "不得使用素材包证据之外的 id。sections≤12，每节 purpose≤40字。"
           + JSON_RULE + f" 输出结构：{schema}"),
        _u(f"任务目标：{goal}\n\n素材包：\n{material_block}\n\n请输出提纲 JSON。"),
    ]


# ---- 初稿（S3-06/02） ---------------------------------------------------
def build_draft_messages(goal: str, outline_block: str, material_block: str,
                         previous_report: str = "", revision_notes: str = "") -> list[dict]:
    schema = '{"report_markdown":"完整 Markdown 报告正文（含标题）"}'
    extra = ""
    if previous_report:
        extra = (f"\n\n这是需要修订的上一稿（请按问题清单修订，保持引用可定位）：\n"
                 f"--- 上一稿开始 ---\n{previous_report}\n--- 上一稿结束 ---"
                 f"\n\n问题清单：\n{revision_notes}")
    return [
        _s(GOAL_RULES + " 你是报告写作者。按提纲逐节写作；每个事实性断言后标注引用标记"
           "【[E-编号]】（只能使用素材包与提纲中出现的证据）；要求标注事实/推断的章节，"
           "在相应表述后加〔事实〕/〔推断〕/〔未知〕。章节标题必须与提纲一致，不能缺节。"
           "【篇幅】只写提纲要求的章节；整份 Markdown 正文尽量控制在 2500 字以内，超长请精简；"
           "引号等特殊字符无需转义（JSON 字符串内直接写中文标点）。"
           + JSON_RULE + f" 输出结构：{schema}"),
        _u(f"任务目标：{goal}\n\n提纲：\n{outline_block}\n\n素材包：\n{material_block}{extra}"),
    ]


# ---- 审校（S3-09/10） ---------------------------------------------------
def build_review_messages(goal: str, report: str, evidence_index: str,
                          outline_requirements: str) -> list[dict]:
    schema = ('{"issues":[{"severity":"error|warn","code":'
              '"support|missing|conflict|style","message":"问题与位置"}],'
              '"verdict":"accepted|needs_revision"}')
    return [
        _s(GOAL_RULES + " 你是审校员。逐条检查：引用是否真的支持该断言（support）、"
           "必需内容是否缺失（missing）、是否有未标注的矛盾（conflict）、表述与格式（style）。"
           "error=会导致读者被误导/要求未满足；warn=建议改进。verdict：存在 error 必须 needs_revision。"
           "issues≤15 条且每条 message≤80字。"
           + JSON_RULE + f" 输出结构：{schema}"),
        _u(f"任务目标：{goal}\n\n证据索引（evidence_id/fact/source）：\n{evidence_index}\n"
           f"\n提纲要求：\n{outline_requirements}\n\n报告正文：\n{report}\n\n请给出审校 JSON。"),
    ]


def format_evidence_block(items: list[dict]) -> str:
    lines = []
    for item in items:
        source = item.get("source_id", "?")
        lines.append(f"- {item['evidence_id']} [tag={item['tag']}] {item['fact']} "
                     f"(来源 {source}, 摘录: {item.get('quote', '')[:120]})")
    return "\n".join(lines) if lines else "（没有可用证据）"


def format_material_block(pack: dict) -> str:
    return json.dumps(pack, ensure_ascii=False, indent=1)[:20000]


def _field(obj, key: str, default=""):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def format_outline_requirements(sections: list) -> str:
    lines = []
    for section in sections:
        heading = _field(section, "heading", "")
        purpose = _field(section, "purpose", "")
        required = _field(section, "required_evidence", []) or []
        markers = bool(_field(section, "require_fact_markers", False))
        lines.append(f"{heading}：{purpose}"
                     f"；必须覆盖证据 {','.join(required) if required else '无'}"
                     + ("；需事实/推断标注" if markers else ""))
    return "\n".join(lines)
