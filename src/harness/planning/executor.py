# -*- coding: utf-8 -*-
"""
harness/planning/executor.py —— Plan-and-Execute（DEV_PLAN C5 / 步骤 41-42，44 指标）

执行循环（确定性调度 + 失败观察 + 有限重规划）：
    Scheduler.next_ready() → 逐任务执行 → 成功 COMPLETED / 失败 FAILED
    → 出现 FAILED → Replanner 生成差异 → apply → 继续
    → 全部 COMPLETED / 重规划预算耗尽 → PlanResult

task_runner(task) 是可注入的执行函数：真实场景里按 preferred_agent 派发给
对应 Agent（单智能体 Runtime 的子任务执行即默认实现）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from src.harness.model_gateway import BudgetStop, role_scope

from src.harness.planning.planner import plan_task
from src.harness.planning.replanner import replan
from src.harness.planning.scheduler import Scheduler
from src.harness.planning.task import (COMPLETED, FAILED, PENDING, READY,
                                       RUNNING, BLOCKED, SKIPPED, Plan, Task)
from src.harness.planning.task_graph import TaskGraph


@dataclass
class PlanResult:
    success: bool
    plan: Plan
    executed: int = 0
    replans: int = 0
    attempts_total: int = 0
    failures: list = field(default_factory=list)   # [(task_id, error)]
    no_progress: bool = False

    def metrics(self) -> dict:
        """Planning Eval（C7 雏形 / 步骤 44）。"""
        tasks = self.plan.tasks
        completed = [t for t in tasks if t.status == COMPLETED]
        failed = [t for t in tasks if t.status == FAILED]
        unnecessary = [t for t in tasks if t.status == SKIPPED]
        return {
            "task_count": len(tasks),
            "completed": len(completed),
            "plan_completion_rate": (len(completed) / len(tasks)) if tasks else None,
            "failed_tasks": len(failed),
            "unnecessary_task_rate": (len(unnecessary) / len(tasks)) if tasks else None,
            "replan_count": self.replans,
            "executed_steps": self.executed,
            "replan_success": self.replans > 0 and self.success,
            "plan_version": self.plan.version,
            "invalidated_tasks": list(self.plan.invalidated_task_ids),
            "no_progress": self.no_progress,
        }


def default_task_runner(llm):
    """默认执行：把每个子任务交给单智能体 Runtime（一次独立 run）。"""
    from src.harness.runtime.agent_runtime import AgentRuntime

    runtime = AgentRuntime(llm)

    def runner(task: Task) -> str:
        with role_scope(task.preferred_agent):
            outcome = runtime.run_task(task.description)
        if outcome.termination_reason == "budget_exceeded":
            raise BudgetStop("根任务限制已触发")
        if outcome.termination_reason != "success":
            raise RuntimeError(f"任务未成功终止：{outcome.termination_reason}")
        return outcome.final_text

    return runner


def execute_plan(llm, plan: Plan, *, runner=None,
                 max_replans: int = 2, max_attempts_per_task: int = 2,
                 workspace_note: str = "") -> PlanResult:
    runner = runner or default_task_runner(llm)
    graph = TaskGraph(plan)
    scheduler = Scheduler(graph)
    failures: list[tuple[str, str]] = []
    replans = 0
    executed = 0
    guard = 0
    no_progress = False

    def plan_fingerprint(candidate: Plan) -> tuple:
        return tuple((t.id, t.description, tuple(t.depends_on), t.priority)
                     for t in candidate.tasks)

    seen_plans = {plan_fingerprint(plan)}
    no_progress = False

    def plan_fingerprint(candidate: Plan) -> tuple:
        return tuple((t.id, t.description, tuple(t.depends_on), t.priority)
                     for t in candidate.tasks)

    seen_plans = {plan_fingerprint(plan)}

    while not graph.is_finished() and guard < len(plan.tasks) * 6 + 6:
        guard += 1
        task = scheduler.next_one()
        if task is None:
            # 没有就绪任务但也没全完成：要么 blocked（等重规划），要么有循环风险
            blocked = graph.blocked_tasks()
            if blocked and replans < max_replans:
                replans += 1
                new_plan = replan(llm, plan, [b for b in blocked if b.status == PENDING])
                fingerprint = plan_fingerprint(new_plan)
                if fingerprint in seen_plans:
                    no_progress = True
                    break
                seen_plans.add(fingerprint)
                plan = new_plan
                graph = TaskGraph(plan)
                scheduler = Scheduler(graph)
                continue
            break
        task.transition(RUNNING)
        executed += 1
        try:
            result = runner(task)
            task.transition(COMPLETED)
            task.result = str(result)
        except BudgetStop:
            raise
        except Exception as e:  # noqa: BLE001 —— 失败观察：记录但不崩溃
            task.transition(FAILED)
            task.error = str(e)
            failures.append((task.id, str(e)))
            if len(failures) and replans < max_replans \
                    and task.attempts < max_attempts_per_task:
                replans += 1
                new_plan = replan(llm, plan, [task])
                fingerprint = plan_fingerprint(new_plan)
                if fingerprint in seen_plans:
                    no_progress = True
                else:
                    seen_plans.add(fingerprint)
                    plan = new_plan
                    graph = TaskGraph(plan)
                    scheduler = Scheduler(graph)
                # 重规划后从当前节点继续循环（失败任务已 reset 为 PENDING）
        # 更新 graph/scheduler 引用（可能被重规划替换）
        graph = TaskGraph(plan)
        scheduler = Scheduler(graph)

    graph.mark_blocked_cascade()
    success = graph.all_completed()
    return PlanResult(success=success, plan=plan, executed=executed,
                      replans=replans, failures=failures, no_progress=no_progress)


def plan_and_execute(llm, user_task: str, *, runner=None,
                     max_replans: int = 2) -> PlanResult:
    """一步到位：Planner → Executor（步骤 41 的入口）。"""
    plan = plan_task(llm, user_task)
    return execute_plan(llm, plan, runner=runner, max_replans=max_replans)
