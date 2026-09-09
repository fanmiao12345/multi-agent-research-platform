# -*- coding: utf-8 -*-
"""
harness/planning/replanner.py —— Replanner（DEV_PLAN C6 / 步骤 43）

触发条件：Task 失败 / 证据不足 / Reviewer REWORK / 发现新依赖 / 预算不足。
原则：Replan 不做全量重来，而是产出“差异”：
    add_tasks / remove_tasks / modify_tasks / reprioritize
apply_delta() 是纯函数式应用器（不依赖 LLM，可直接单测）。
"""

from __future__ import annotations

from functools import partial
from src.harness.model_gateway import model_call, BudgetStop

call_model = partial(model_call, purpose="replanner", role="replanner")

from src.harness.planning.task import FAILED, PENDING, Plan, Task
from src.harness.structured import extract_json
from src.llm.mock import MockLLM

REPLANNER_SYSTEM = """你是重规划器。一个子任务失败了，请生成计划差异（JSON 对象，不要其它内容）：
{
  "add_tasks": [{"description": "...", "depends_on": [], "preferred_agent": "agent", "priority": 1}],
  "remove_tasks": ["T2"],
  "modify_tasks": [{"id": "T2", "description": "失败任务 T2 的替代做法"}],
  "reprioritize": {"T3": 0}
}
规则：能修就 modify 失败任务本身；需要补步骤就 add_tasks；说明理由放在 description 里。"""


def apply_delta(plan: Plan, delta: dict) -> Plan:
    """把差异应用到 Plan（返回新 Plan；纯函数，失败任务被重置可重试）。"""
    plan = Plan.from_dict(plan.to_dict())  # 深拷贝，避免副作用
    next_num = max((int(t.id[1:]) for t in plan.tasks if t.id[1:].isdigit()), default=0) + 1

    for raw in delta.get("add_tasks", []):
        tid = f"T{next_num}"
        plan.tasks.append(Task(id=tid,
                               description=raw.get("description", "补查任务"),
                               depends_on=list(raw.get("depends_on") or []),
                               preferred_agent=raw.get("preferred_agent", "agent")))
        next_num += 1

    remove = set(delta.get("remove_tasks", []))
    plan.tasks = [t for t in plan.tasks if t.id not in remove]

    for mod in delta.get("modify_tasks", []):
        task = plan.by_id(str(mod["id"]))
        if task:
            task.description = mod.get("description", task.description)
            task.reset()  # FAILED -> PENDING（attempts+1，防死循环由执行器控制）
            if mod.get("depends_on") is not None:
                task.depends_on = list(mod["depends_on"])

    for tid, priority in (delta.get("reprioritize") or {}).items():
        task = plan.by_id(str(tid))
        if task:
            task.priority = int(priority)
    return plan


def _mock_delta(plan: Plan, failures: list[Task]) -> dict:
    """Mock 重规划：对每个失败任务 produce 一个“换做法重试”的 modify。"""
    modify = []
    for task in failures:
        if task.attempts >= 1:
            # 已经重试过一次仍失败：不再无脑重试，改为收尾总结任务
            modify.append({"id": task.id,
                           "description": f"（降级）{task.description} —— 改用已有信息做收尾"})
        else:
            modify.append({"id": task.id,
                           "description": f"（重试）{task.description} —— 换一种更简单的方式"})
    return {"add_tasks": [], "remove_tasks": [], "modify_tasks": modify,
            "reprioritize": {}}


def replan(llm, plan: Plan, failures: list[Task]) -> Plan:
    if isinstance(llm, MockLLM):
        return apply_delta(plan, _mock_delta(plan, failures))
    text = ""
    try:
        reply = call_model(llm, [{"role": "system", "content": REPLANNER_SYSTEM},
                          {"role": "user",
                           "content": f"当前计划：\n{plan.summary()}\n"
                                      f"失败任务：{[(t.id, t.error[:120]) for t in failures]}"}])
        text = reply.content or ""
    except BudgetStop:
        raise
    except Exception:
        text = ""
    data = extract_json(text)
    return apply_delta(plan, data or _mock_delta(plan, failures))
