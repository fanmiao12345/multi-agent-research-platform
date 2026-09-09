# -*- coding: utf-8 -*-
"""
harness/skills/router.py —— Skill Routing（步骤 52）

两级路由：BM25 Recall（recall.py）→ LLM Rerank → Skill Injection。
路由决策可观测：记录 candidates（含分数）、最终选择与理由（供 Trace/Eval）。
Mock 大脑取 recall 第一名（规则 = 极简 rerank），离线可全流程演示。
"""

from __future__ import annotations

from functools import partial
from src.harness.model_gateway import model_call, BudgetStop

call_model = partial(model_call, purpose="rerank", role="rerank")

import re

from src.harness.skills.recall import recall
from src.harness.skills.registry import SkillRegistry
from src.llm.mock import MockLLM

ROUTER_SYSTEM = """你是技能路由器。根据用户问题，从候选技能里选一个最合适的启用。

规则：
1. 只从候选技能里选；都不合适就回答 NO_SKILL；
2. 某技能 description 与用户请求明显匹配且能帮上忙才选；
3. 一次只选一个。

只输出一行：USE_SKILL:技能名   或   NO_SKILL"""


def _candidate_lines(cands: list[dict]) -> str:
    registry_meta = {}
    return "\n".join(f"- {c['name']}（候选分 {c['score']}）" for c in cands)


def route(registry: SkillRegistry, llm, question: str, top_k: int = 5) -> dict:
    cands = recall(registry, question, top_k=top_k)
    if not cands:
        return {"skill": None, "candidates": [], "reason": "召回为空"}

    if isinstance(llm, MockLLM):
        chosen = cands[0]
        return {"skill": chosen["name"],
                "candidates": cands,
                "reason": "召回命中词: " + "、".join(chosen["matched"][:5])}

    skill_lines = "\n".join(
        f"- {c['name']}：{registry.get(c['name']).description if registry.get(c['name']) else ''}"
        for c in cands)
    try:
        reply = call_model(llm, [
            {"role": "system", "content": ROUTER_SYSTEM + "\n\n候选技能：\n" + skill_lines},
            {"role": "user", "content": question}])
        text = reply.content or ""
    except BudgetStop:
        raise
    except Exception:  # noqa: BLE001 —— 路由失败兜底取 recall 第一
        chosen = cands[0]
        return {"skill": chosen["name"], "candidates": cands, "reason": "rerank 失败，用召回第一"}

    m = re.search(r"USE_SKILL\s*[:：]?\s*([A-Za-z0-9_-]+)", text)
    if m and registry.get(m.group(1)):
        return {"skill": m.group(1), "candidates": cands,
                "reason": "rerank 选择: " + m.group(1)}
    for c in cands:
        if c["name"] in text and "NO_SKILL" not in text.upper():
            return {"skill": c["name"], "candidates": cands, "reason": "宽松匹配"}
    return {"skill": None, "candidates": cands, "reason": "rerank 判定 NO_SKILL"}


def inject(skill) -> str:
    """把命中的技能正文拼成可注入上下文的 system 片段（Skill Injection）。"""
    return (f"\n\n[已启用技能：{skill.name}]（{skill.description}）\n"
            f"仅在本任务遵循以下说明：\n{skill.instructions}")
