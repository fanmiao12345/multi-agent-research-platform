# -*- coding: utf-8 -*-
"""
application/pipeline/material.py —— 素材包阶段（S3-05）

- 模型按主题组织证据、指出冲突与缺口；程序校验 evidence_id 全部真实存在；
- 冲突不得被模型"解决"：status 强制 open，出现 resolved/closed 一律改回并记 issue
  （不允许无依据消解矛盾，材料缺什么如实列 gap）；
- 转载/重复来源由程序从来源登记直接生成（不占模型调用）。
"""
from __future__ import annotations

from src.application.pipeline.model import MaterialPack, StageError
from src.application.pipeline.prompts import build_material_messages
from src.harness.model_gateway import model_call
from src.harness.structured import extract_json

MAX_ITEMS = 200


def run_material_stage(llm, goal: str, evidence_items: list[dict],
                       labels: dict[str, str] | None = None) -> tuple[MaterialPack, list[dict]]:
    """返回 (素材包, issues)；模型输出结构非法抛 StageError。

    labels：source_id → 可读来源名；用于把证据列表渲染给模型，避免内部 id
    被抄进素材包 statement（Q3-01 第 2 批根因，见 prompts.format_evidence_block）。
    """
    from src.application.pipeline.prompts import format_evidence_block
    known = {item["evidence_id"] for item in evidence_items}
    block = format_evidence_block(evidence_items, labels)
    data = None
    raw = ""
    for attempt in (1, 2):
        messages = build_material_messages(goal, block)
        if attempt == 2:
            messages = messages[:1] + [{
                "role": "system",
                "content": "上一次输出无法解析为 JSON。这次只输出一个紧凑、完整的 JSON"
                           "对象（topics≤6、每主题 points≤6），不要围栏与解释。"}] \
                + messages[1:]
        reply = model_call(llm, messages,
                           purpose="material_pack", role="material")
        raw = reply.content or ""
        data = extract_json(raw)
        if data is not None:
            break
    issues: list[dict] = []
    if not data:
        raise StageError("material", "素材包输出不是合法 JSON 对象"
                         + (f"；原始回复片段：{raw[:200]}" if raw else ""))
    topics = data.get("topics") if isinstance(data.get("topics"), list) else []
    pack = MaterialPack()
    for topic in topics:
        if not isinstance(topic, dict):
            issues.append({"severity": "warn", "code": "format",
                           "message": "素材主题不是对象，已跳过"})
            continue
        name = (topic.get("name") or "").strip()
        points = []
        for point in (topic.get("points") or []) if isinstance(topic.get("points"), list) else []:
            if not isinstance(point, dict):
                continue
            evidence_id = (point.get("evidence_id") or "").strip()
            statement = (point.get("statement") or "").strip()
            if evidence_id not in known:
                issues.append({"severity": "error", "code": "citation",
                               "message": f"素材引用了不存在的证据 {evidence_id}，已丢弃"})
                continue
            if not statement:
                issues.append({"severity": "warn", "code": "format",
                               "message": f"{evidence_id} 的陈述为空，已丢弃"})
                continue
            points.append({"evidence_id": evidence_id, "statement": statement})
            if len(points) >= MAX_ITEMS:
                break
        if name or points:
            pack.topics.append({"name": name or "（未命名主题）", "points": points})
    # 冲突：只允许 open（S3-05 无依据消解矛盾禁令）
    for conflict in data.get("conflicts") or []:
        if not isinstance(conflict, dict):
            continue
        statement = (conflict.get("statement") or "").strip()
        ids = [x for x in (conflict.get("evidence_ids") or []) if isinstance(x, str)]
        if not statement or len(ids) < 2 or any(x not in known for x in ids):
            issues.append({"severity": "warn", "code": "format",
                           "message": "冲突条目缺少表述或证据 id 无效，已丢弃"})
            continue
        status = str(conflict.get("status") or "open").strip().lower()
        if status not in ("open",):
            issues.append({"severity": "error", "code": "conflict",
                           "message": f"模型试图把冲突 {ids} 记为 {status}；"
                                      "无依据的消解不被允许，已强制为 open 并列入缺口"})
        pack.conflicts.append({"statement": statement, "evidence_ids": ids,
                               "status": "open"})
    for gap in data.get("gaps") or []:
        if not isinstance(gap, dict):
            continue
        question = (gap.get("question") or "").strip()
        if question:
            pack.gaps.append({"question": question,
                              "missing": (gap.get("missing") or "").strip()})
    return pack, issues


def fill_duplicates(pack: MaterialPack, sources_index: list[dict]) -> list[dict]:
    """从来源登记补重复/转载清单（S3-05：程序判定，不消耗模型调用）。

    Q3-01 第 2 批：duplicate_of 原本是内部 source_id（src_xxxx），渲染进素材包后
    被写进交付正文（o03「粘贴文本 2 与 src_010915e256 实质相同」）。现在统一换成
    可读来源名，交给模型的两侧都是人能看懂的名字。
    """
    labels = {}
    for record in sources_index:
        key = record.get("source_id") or ""
        labels[key] = record.get("display") or record.get("file_name") or "该来源"
    dupes = []
    for record in sources_index:
        if record.get("status") == "duplicate" and record.get("duplicate_of"):
            dupes.append({"display": record.get("display") or "该来源",
                          "duplicate_of": labels.get(record["duplicate_of"],
                                                     "同一来源的另一份材料")})
    pack.duplicates = dupes
    return dupes


def render_material(pack: MaterialPack) -> str:
    lines = ["# 素材包", ""]
    for topic in pack.topics:
        lines.append(f"## {topic['name']}")
        if not topic["points"]:
            lines.append("- （该主题暂无已核实证据）")
        for point in topic["points"]:
            lines.append(f"- [{point['evidence_id']}] {point['statement']}")
        lines.append("")
    if pack.conflicts:
        lines.append("## 冲突（均为开放状态，未获新证据前不作裁决）")
        for conflict in pack.conflicts:
            lines.append(f"- {conflict['statement']}（证据："
                         f"{', '.join(conflict['evidence_ids'])}）")
        lines.append("")
    if pack.gaps:
        lines.append("## 待补问题（缺口）")
        for gap in pack.gaps:
            tail = f"——缺少：{gap['missing']}" if gap.get("missing") else ""
            lines.append(f"- {gap['question']}{tail}")
        lines.append("")
    if pack.duplicates:
        lines.append("## 重复来源（转载按同一份证据处理）")
        for dup in pack.duplicates:
            lines.append(f"- {dup['display']} 与 {dup['duplicate_of']} 实质相同")
        lines.append("")
    return "\n".join(lines)
