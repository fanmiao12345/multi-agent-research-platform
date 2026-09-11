# -*- coding: utf-8 -*-
"""D5-01：任务理解与可恢复的待输入状态。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.harness.run_store import write_json

_REPORT_WORDS = ("报告", "论文", "成稿", "写作", "撰写", "brief", "白皮书")
_COLLECTION_WORDS = ("整理", "目录", "清单", "总结", "归纳", "分类")
_TOOL_WORDS = ("计算", "算一下", "现在几点", "当前时间", "日期")
_COMPARE_WORDS = ("比较", "对比", "compare", "vs")


@dataclass
class TaskUnderstanding:
    goal: str
    work_type: str
    delivery_requirements: dict = field(default_factory=dict)
    material_gaps: list[str] = field(default_factory=list)
    key_conditions: list[str] = field(default_factory=list)
    needs_input: bool = False
    questions: list[str] = field(default_factory=list)
    defaults_applied: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": 1, "goal": self.goal, "work_type": self.work_type,
            "delivery_requirements": dict(self.delivery_requirements),
            "material_gaps": list(self.material_gaps),
            "key_conditions": list(self.key_conditions),
            "needs_input": self.needs_input,
            "questions": list(self.questions),
            "defaults_applied": list(self.defaults_applied),
        }


def _length_requirement(task: str) -> int | None:
    match = re.search(r"(\d{2,6})\s*(?:字|words?|词)", task, re.I)
    return int(match.group(1)) if match else None


def understand_task(request, *, network_available: bool | None = None) -> TaskUnderstanding:
    """确定性理解任务；只在无法确定对象/必要资料时进入待输入。"""
    task = (request.task or "").strip()
    source_count = len(request.texts) + len(request.files) + len(request.urls)
    if network_available is None:
        network_available = bool(request.allow_network)
    delivery = {
        "required_sections": list(request.required_sections),
        "forbidden_claims": list(request.forbidden_claims),
        "key_facts": list(request.key_facts),
    }
    length = _length_requirement(task)
    defaults_applied: list[str] = []
    if length:
        delivery["target_length"] = length
    else:
        delivery["target_length"] = "按任务与提纲自适应"
        defaults_applied = ["未指定字数，按任务复杂度与提纲自适应"]
    if request.flow == "research":
        if request.base_draft or request.revises_job:
            work_type = "revision"
        elif any(w in task.lower() for w in _COLLECTION_WORDS) and not any(
                w in task.lower() for w in _REPORT_WORDS):
            work_type = "collection"
        elif any(w in task.lower() for w in _REPORT_WORDS):
            work_type = "research_report"
        else:
            work_type = "research"
    elif any(w in task for w in _TOOL_WORDS):
        work_type = "simple_tool"
    else:
        work_type = "simple_qa"

    material_gaps: list[str] = []
    key_conditions: list[str] = []
    material_questions: list[str] = []
    key_questions: list[str] = []
    if request.flow == "research" and source_count == 0:
        if not request.allow_network:
            material_gaps.append("未提供资料，且未允许联网")
            material_questions.append("请补充资料，或明确允许联网研究。")
        elif not network_available:
            material_gaps.append("允许联网但搜索服务未配置")
            material_questions.append("请配置 SEARCH_PROVIDER，或补充本地资料。")
    if any(w in task.lower() for w in _COMPARE_WORDS) and not re.search(
            r"(?:与|和|vs\.?|、)", task, re.I):
        key_conditions.append("比较对象不明确")
        key_questions.append("请说明要比较的具体对象或范围。")
    if task in ("继续", "继续处理", "优化一下", "处理一下") or len(task) < 4:
        key_conditions.append("任务对象/动作不明确")
        key_questions.append("请补充要处理的对象和期望结果。")

    deduped_questions = list(dict.fromkeys(material_questions + key_questions))
    return TaskUnderstanding(
        goal=task, work_type=work_type, delivery_requirements=delivery,
        material_gaps=material_gaps, key_conditions=key_conditions,
        needs_input=bool(key_questions), questions=deduped_questions,
        defaults_applied=defaults_applied)


def persist_input_request(job_dir: str | Path, understanding: TaskUnderstanding) -> Path:
    """把待输入状态写进任务目录，供后续携带补充条件恢复。"""
    path = Path(job_dir) / "input_request.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, understanding.to_dict())
    return path


def load_input_request(job_dir: str | Path) -> dict | None:
    path = Path(job_dir) / "input_request.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise ValueError("input_request.json 损坏，拒绝伪装为无需输入") from None