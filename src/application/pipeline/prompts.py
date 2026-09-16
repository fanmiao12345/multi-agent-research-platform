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

# Q3-01 第 2 批（O-08 ③）：交付正文与章节标题里出现内部标识是实测缺陷。
# 提纲/写作阶段统一红线：只许用可读来源名与 [E-编号]，不许出现内部 id 与字段名。
NO_INTERNAL_ID_RULE = ("【禁止内部标识】章节标题、purpose 与正文里都不得出现 src_/job_ "
                       "等内部 id，也不得出现 evidence_id、artifact_id、pipeline.json、"
                       "duplicates 等内部字段名；指代资料时用给定的来源名称"
                       "（如「粘贴文本 2」），引用证据只用 [E-编号]。")


def _s(content: str) -> dict:
    return {"role": "system", "content": content}


def _u(content: str) -> dict:
    return {"role": "user", "content": content}


# ---- 证据提取（S3-04） --------------------------------------------------
def build_evidence_messages(goal: str, source: dict) -> list[dict]:
    """source: {source_id,title,display,text(预算内),truncated:bool}

    Q3-01 第 2 批根因：这里原本把 `id=src_xxxx` 交给提取器，模型会把内部来源 id
    抄进 fact 文本（实测 o07「来源src_01b5f94ffb标注本材料为合成验收数据」），
    再经素材包流入交付正文。现在只给可读来源名，并要求指代资料时用「本资料」。
    """
    note = ("（注意：该来源过长，只提供了前段文本；不得猜测未提供的后段内容，"
            "如需可如实写入缺口。）" if source.get("truncated") else "")
    schema = ('{"items":[{"fact":"支持的事实/主张，一句话≤60字","tag":"F|I|U",'
              '"quote":"原文连续片段，必须与资料逐字一致，≤80字符"}]}')
    return [
        _s(GOAL_RULES + " 你是证据提取器。给出来源中与本任务相关的事实主张；"
           "tag：F=资料直接支持的事实，I=有依据的推断，U=资料未说明、只是你的猜测（尽量少）。"
           "【表述】fact 里指代材料时写「本资料」，不得出现 src_、job_ 等内部标识"
           "或 evidence_id/artifact_id 等字段名。"
           f"【数量与长度】items 最多 8 条；fact 一句话≤60字；quote ≤80字符；"
           f"不要输出示例之外的任何内容。{JSON_RULE} 输出结构：{schema}"),
        _u(f"任务目标：{goal}\n\n来源名称：{source['display']}"
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
def build_outline_messages(goal: str, material_block: str,
                           requirements_block: str = "") -> list[dict]:
    schema = ('{"title":"报告标题","sections":[{"heading":"章节标题",'
              '"purpose":"本节要回答什么","required_evidence":["E-001"],'
              '"require_fact_markers":true}],'
              '"cannot_answer":{"reason":"证据完全无法回答任务的原因",'
              '"missing":["缺失的信息"]}}')
    hard = ("【任务硬性要求】若任务列出了必须出现的章节，提纲必须逐字包含这些章节名"
            "（可另加章节，但不能改名或省略）。" if requirements_block else "")
    refuse = ("【无法完成出口】只有当素材包证据完全无法支撑任务目标（不是部分不足）"
              "时，才可省略 sections 并输出 cannot_answer，reason≤80字，"
              "missing 列出缺失信息；不得为了省事而拒绝能完成的任务。")
    return [
        _s(GOAL_RULES + " 你是提纲规划器。根据素材包设计固定顺序的报告提纲；"
           "每个章节给出必须覆盖的证据 id 与是否要求正文标注事实/推断/未知。"
           "不得使用素材包证据之外的 id。sections≤12，每节 purpose≤40字。"
           + NO_INTERNAL_ID_RULE + hard + refuse + JSON_RULE + f" 输出结构：{schema}"),
        _u(_join_blocks(f"任务目标：{goal}", requirements_block,
                        f"素材包：\n{material_block}") + "\n\n请输出提纲 JSON。"),
    ]


# ---- 初稿（S3-06/02） ---------------------------------------------------
def build_draft_messages(goal: str, outline_block: str, material_block: str,
                         previous_report: str = "", revision_notes: str = "",
                         requirements_block: str = "") -> list[dict]:
    schema = '{"report_markdown":"完整 Markdown 报告正文（含标题）"}'
    extra = ""
    if previous_report:
        extra = (f"\n\n这是需要修订的上一稿（请按问题清单修订，保持引用可定位）：\n"
                 f"--- 上一稿开始 ---\n{previous_report}\n--- 上一稿结束 ---"
                 f"\n\n问题清单：\n{revision_notes}")
    hard = ("【任务硬性要求】必须逐条满足：必需章节的标题要与要求逐字一致"
            "（不要加序号或改写），禁止表述一律不得出现。" if requirements_block else "")
    return [
        _s(GOAL_RULES + " 你是报告写作者。按提纲逐节写作；每个事实性断言后标注引用标记"
           "【[E-编号]】（只能使用素材包与提纲中出现的证据）；要求标注事实/推断的章节，"
           "在相应表述后加〔事实〕/〔推断〕/〔未知〕。章节标题必须与提纲一致（逐字使用，"
           "不要自行添加编号前缀或改写），不能缺节。"
           + NO_INTERNAL_ID_RULE + hard +
           "【篇幅】只写提纲要求的章节；整份 Markdown 正文尽量控制在 2500 字以内，超长请精简；"
           "引号等特殊字符无需转义（JSON 字符串内直接写中文标点）。"
           + JSON_RULE + f" 输出结构：{schema}"),
        _u(_join_blocks(f"任务目标：{goal}", requirements_block,
                        f"提纲：\n{outline_block}", f"素材包：\n{material_block}")
           + extra),
    ]


# ---- 审校（S3-09/10） ---------------------------------------------------
def build_review_messages(goal: str, report: str, evidence_index: str,
                          outline_requirements: str,
                          requirements_block: str = "") -> list[dict]:
    schema = ('{"issues":[{"severity":"error|warn","code":'
              '"support|missing|conflict|style","message":"问题与位置"}],'
              '"verdict":"accepted|needs_revision"}')
    hard = ("任务硬性要求（请一并核对是否满足，未满足须报 error/missing）：\n"
            + requirements_block if requirements_block else "")
    return [
        _s(GOAL_RULES + " 你是审校员。逐条检查：引用是否真的支持该断言（support）、"
           "必需内容是否缺失（missing）、是否有未标注的矛盾（conflict）、表述与格式（style）。"
           "error=会导致读者被误导/要求未满足；warn=建议改进。verdict：存在 error 必须 needs_revision。"
           "issues≤15 条且每条 message≤80字。"
           + JSON_RULE + f" 输出结构：{schema}"),
        _u(_join_blocks(f"任务目标：{goal}", hard,
                        f"证据索引（evidence_id/fact/source）：\n{evidence_index}",
                        f"提纲要求：\n{outline_requirements}")
           + f"\n\n报告正文：\n{report}\n\n请给出审校 JSON。"),
    ]


def _join_blocks(*blocks: str) -> str:
    """按顺序拼接非空块，块间空一行（避免出现空块与重复空行）。"""
    return "\n\n".join(block.strip() for block in blocks if block and block.strip())


def format_evidence_block(items: list[dict], labels: dict[str, str] | None = None) -> str:
    """把证据列表渲染成可读文本；labels: source_id → 可读来源名（如「粘贴文本 2」）。

    Q3-01 第 2 批根因：此处原来直接写 source_id（src_xxxx），模型会把它抄进提纲标题、
    素材包 statement 与交付正文（实测 o01「资料一：src_edfac4b498」、o07 正文同款）。
    现在优先用可读来源名；拿不到映射时写「该来源」，绝不把内部 id 交给写作/提纲模型。
    """
    lines = []
    mapping = labels or {}
    for item in items:
        source = mapping.get(item.get("source_id") or "") or "该来源"
        lines.append(f"- {item['evidence_id']} [tag={item['tag']}] {item['fact']} "
                     f"(来源 {source}, 摘录: {item.get('quote', '')[:120]})")
    return "\n".join(lines) if lines else "（没有可用证据）"


def format_material_block(pack: dict) -> str:
    """把素材包渲染成**面向写作的自然语言**（不再直接塞原始 JSON）。

    实测问题（Q3-01 第 2 批）：此前直接给模型原始 JSON，键名（duplicates/evidence_id/
    points 等）会被写进交付正文，出现"素材包 duplicates 字段记录…"这类内部标识泄漏；
    这里改为人类可读渲染，内部字段名不出现在提示词里，从源头消除泄漏。
    仍保留 20000 字符上限。
    """
    lines: list[str] = []
    topics = pack.get("topics") or []
    if topics:
        lines.append("## 已核实的证据（按主题组织，方括号内为引用编号）")
        for topic in topics:
            lines.append(f"### {topic.get('name') or '（未命名主题）'}")
            for point in topic.get("points") or []:
                lines.append(f"- [{point.get('evidence_id')}] {point.get('statement')}")
        lines.append("")
    conflicts = pack.get("conflicts") or []
    if conflicts:
        lines.append("## 冲突（均为开放状态，未获新证据前不作裁决）")
        for conflict in conflicts:
            ids = "、".join(conflict.get("evidence_ids") or [])
            lines.append(f"- {conflict.get('statement')}（涉及 {ids}）")
        lines.append("")
    gaps = pack.get("gaps") or []
    if gaps:
        lines.append("## 待补问题（材料缺口）")
        for gap in gaps:
            tail = f"——缺少：{gap.get('missing')}" if gap.get("missing") else ""
            lines.append(f"- {gap.get('question')}{tail}")
        lines.append("")
    duplicates = pack.get("duplicates") or []
    if duplicates:
        lines.append("## 重复来源（转载按同一份证据处理，不得重复计数）")
        for dup in duplicates:
            lines.append(f"- {dup.get('display')} 与 {dup.get('duplicate_of')} 实质相同")
        lines.append("")
    text = "\n".join(lines) if lines else "（没有整理出素材，请如实说明材料不足）"
    return text[:20000]


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
