# -*- coding: utf-8 -*-
"""
eval/evaluators/skill_metrics.py —— Skill Eval（DEV_PLAN D9 / 步骤 53）

按 kind 分组统计路由正确率：
- positive：期望命中该技能（route.skill == skill）
- negative：期望不命中（skill 为 None）
- confusion：两技能都像，按 label 判定（命中 label 视为正确）

Mock 大脑下是“recall@1 正确率”；真实模型下是“rerank 正确率”。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.harness.skills.registry import SkillRegistry

CASES_PATH = Path(__file__).resolve().parent.parent / "datasets" / "skill_cases_v1.json"


def load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


def evaluate(registry: SkillRegistry, llm, cases: list[dict] | None = None) -> dict:
    from src.harness.skills.router import route

    cases = cases or load_cases()
    rows = []
    stats = {}
    for case in cases:
        decision = route(registry, llm, case["question"])
        got = decision["skill"]
        kind = case["kind"]
        if kind == "positive":
            ok = got == case["skill"]
        elif kind == "negative":
            ok = got is None
        else:  # confusion
            ok = got == case["skill"]
        rows.append({"id": case["id"], "kind": kind, "expected": case["skill"],
                     "got": got, "ok": ok, "reason": decision["reason"][:60]})
        stats.setdefault(kind, {"total": 0, "ok": 0})
        stats[kind]["total"] += 1
        stats[kind]["ok"] += int(ok)

    out = {}
    for kind, v in stats.items():
        out[kind] = {**v, "accuracy": v["ok"] / v["total"] if v["total"] else None}
    total = sum(v["total"] for v in stats.values())
    ok_all = sum(v["ok"] for v in stats.values())
    out["overall"] = {"total": total, "ok": ok_all,
                      "accuracy": ok_all / total if total else None}
    out["rows"] = rows
    return out
