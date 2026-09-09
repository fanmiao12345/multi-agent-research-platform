# -*- coding: utf-8 -*-
"""
harness/planning/planner.py —— Planner（DEV_PLAN C1 / 步骤 37）

输入复杂任务 → 输出结构化 Plan（goal + tasks）。
真实模型走一次结构化输出调用；Mock 用确定性启发式（查资料→整理→成稿链）。
解析/校验失败一律兜底成单任务计划 —— Planner 出错不允许拖垮执行。
"""

from __future__ import annotations

from functools import partial
from src.harness.model_gateway import model_call, BudgetStop

call_model = partial(model_call, purpose="planner", role="planner")

from src.harness.planning.task import Plan, Task
from src.harness.structured import extract_json
from src.llm.mock import MockLLM

PLANNER_SYSTEM = """你是规划器。把用户任务拆成结构化计划。输出一个 JSON 对象（不要其它内容）：

{
  "goal": "任务目标一句话",
  "tasks": [
    {"id": "T1", "description": "子任务描述", "depends_on": [],
     "preferred_agent": "researcher|organizer|writer|agent", "priority": 1}
  ]
}

规则：
1. id 用 T1/T2/…；depends_on 引用其它 task 的 id（没有依赖就空数组）；
2. 任务粒度：每个子任务应能独立执行并产出文本结果；
3. 有先后依赖就写 depends_on，可并行的任务之间不要写依赖；
4. 复杂度低的任务允许只输出一个任务 T1。"""


def _mock_plan(user_task: str) -> Plan:
    """Mock 启发式：检测"查资料/写文章"类请求 -> 标准三步链，否则单任务。"""
    if any(k in user_task for k in ("调研", "查资料", "搜索", "报告", "写一篇", "写文章", "成稿")):
        chain = [
            Task(id="T1", description="查资料：" + user_task, preferred_agent="researcher"),
            Task(id="T2", description="整理素材", depends_on=["T1"], preferred_agent="organizer"),
            Task(id="T3", description="成稿：" + user_task, depends_on=["T2"],
                 preferred_agent="writer"),
        ]
        return Plan(goal=user_task, tasks=chain)
    return Plan(goal=user_task, tasks=[Task(id="T1", description=user_task)])


def plan_task(llm, user_task: str) -> Plan:
    if isinstance(llm, MockLLM):
        return _mock_plan(user_task)
    try:
        reply = call_model(llm, [{"role": "system", "content": PLANNER_SYSTEM},
                          {"role": "user", "content": f"用户任务：{user_task}"}])
    except BudgetStop:
        raise
    except Exception:
        return Plan(goal=user_task, tasks=[Task(id="T1", description=user_task)])
    data = extract_json(reply.content or "")
    return Plan.from_llm_json(data, fallback_task=user_task) if data \
        else Plan(goal=user_task, tasks=[Task(id="T1", description=user_task)])
