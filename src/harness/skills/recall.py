# -*- coding: utf-8 -*-
"""
harness/skills/recall.py —— BM25-lite Skill Recall（步骤 51）

零依赖关键词召回：中文双字词 + 英文整词；triggers/description 加权；
命中 avoid 类不适用场景扣分暂未引入（技能 schema 无 avoid_when，未来扩展）。
返回带分数与命中词的候选（供 LLM Rerank / Mock 直接取第一）。
"""

from __future__ import annotations

import re
import string

_STOP = set("的一是不了在人有我他这那们来为个中到说上看就要也去会自可很没对好过能多后下子么都得你还".strip())


def tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    tokens = re.findall(r"[a-z0-9_]+", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens += [run[i:i + 2] for i in range(len(run) - 1)]
        if len(run) == 1:
            tokens.append(run)
    return [t for t in tokens if t not in _STOP and len(t) > 1 or (len(t) == 1 and t.isdigit())]


def score_doc(query_tokens: list[str], doc_tokens: list[str],
              weight: int = 1) -> tuple[int, list[str]]:
    qset = set(query_tokens)
    hits = [t for t in query_tokens if t in set(doc_tokens)]
    matched = [t for t in dict.fromkeys(hits)]
    return weight * len(matched), matched


def recall(registry, question: str, top_k: int = 5) -> list[dict]:
    """返回 [{name, score, matched}] 按分数降序（0 分不返回）。"""
    qt = tokenize(question)
    if not qt:
        return []
    scored = []
    for skill in registry.list():
        doc = tokenize(" ".join([
            skill.name, skill.description, " ".join(skill.triggers),
            " ".join(skill.allowed_tools), skill.instructions[:400]]))
        base, matched = score_doc(qt, doc, weight=1)
        trig_tokens = tokenize(" ".join(skill.triggers))
        tri, _ = score_doc(qt, trig_tokens, weight=1)
        score = base + tri * 2  # triggers 加权
        if score > 0:
            scored.append({"name": skill.name, "score": score, "matched": matched})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
