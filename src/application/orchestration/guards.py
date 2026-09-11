# -*- coding: utf-8 -*-
"""
S8-01/03 护栏与预算分配：派生数量/同题去重/嵌套深度 + "整任务分配得下"的额度公式。

额度公式（设计文档 2.D，2026-09-10 审阅后明确）：
    可派工预算 = 根任务剩余额度 − 在途调用预留 − 最终成稿与审校预留
    单子任务 ≤ 可派工预算的 40%，且 Σ(子任务额度) ≤ 可派工预算
调度模型可以建议预算，程序负责核验与扣减。

并行现状（如实限制）：根账本在模型请求期间持有同一把锁（src/harness/model_gateway.py
的 call() 全程持锁），同根任务的模型调用实际串行；因此本层暂按"并发上限校验 +
串行执行"落地，开放真并行前必须先做"请求发起前冻结、返回后结算"（S8-03 决策项）。
"""
from __future__ import annotations

from src.application.orchestration.plan_contract import Budget

SINGLE_CHILD_CAP = 0.4     # 单子任务 ≤ 可派工预算的 40%
FINAL_RESERVE_RATIO = 0.4  # 成稿与审校预留：根预算的 40%（草案，S8-05 实测后校准）


class GuardViolation(ValueError):
    """护栏违规（超限/重复派生）：调用方应记录并降级，不得继续派生。"""


def compute_dispatchable_pool(root_remaining: Budget, *,
                              inflight: Budget | None = None,
                              final_reserve: Budget | None = None) -> Budget:
    """可派工预算 = 根剩余 − 在途预留 − 成稿预留（先给最终交付留额度，再分给子任务）。"""
    pool = root_remaining.subtract(inflight or Budget())
    pool = pool.subtract(final_reserve or Budget())
    return pool


def allocate_budget(pool: Budget, n_children: int, *,
                    single_cap: float = SINGLE_CHILD_CAP) -> list[Budget]:
    """把可派工预算分给 n 个子任务：单个 ≤ 池的 single_cap，且 Σ ≤ 池（构造保证）。

    每份取 min(池*single_cap, 池/n)：n=1 时受 40% 上限保护，n≥3 时按均分自然 ≤ 池。
    """
    if n_children <= 0:
        return []
    per_calls = int(min(pool.max_calls * single_cap, pool.max_calls / n_children))
    per_cost = min(pool.max_cost_usd * single_cap, pool.max_cost_usd / n_children)
    per_seconds = min(pool.max_seconds * single_cap, pool.max_seconds / n_children)
    return [Budget(max_calls=per_calls, max_cost_usd=round(per_cost, 6),
                   max_seconds=round(per_seconds, 6)) for _ in range(n_children)]


def final_reserve_of(root_budget: Budget, *,
                     ratio: float = FINAL_RESERVE_RATIO) -> Budget:
    """最终成稿与审校预留：根预算的 ratio（字段分别取整/保留 6 位）。"""
    return Budget(max_calls=int(root_budget.max_calls * ratio),
                  max_cost_usd=round(root_budget.max_cost_usd * ratio, 6),
                  max_seconds=round(root_budget.max_seconds * ratio, 6))


def _normalize_description(text: str) -> str:
    """同题去重的归一化：去空白与常见标点、转小写，避免"换皮重复派生"。"""
    text = (text or "").strip().lower()
    for ch in "，。；：、！？,. ; : ! ?\n\t":
        text = text.replace(ch, "")
    return text


class OrchestrationGuards:
    """派生护栏：数量上限、同题去重、嵌套深度（根=0，子=1，孙=2）。"""

    def __init__(self, *, max_total_subtasks: int = 12, max_depth: int = 2):
        self.max_total_subtasks = max_total_subtasks
        self.max_depth = max_depth
        self._seen: set[str] = set()
        self.count = 0
        self.violations: list[str] = []

    def register_subtask(self, subtask_id: str, description: str, depth: int = 1) -> None:
        """登记一个子智能体；任何一道锁失效都抛 GuardViolation（调用方负责降级留痕）。"""
        if depth > self.max_depth:
            self.violations.append(f"{subtask_id}: 嵌套深度 {depth} 超上限 {self.max_depth}")
            raise GuardViolation(f"子智能体 {subtask_id} 嵌套深度超限（{depth} > {self.max_depth}）")
        key = _normalize_description(description)
        if key and key in self._seen:
            self.violations.append(f"{subtask_id}: 同题重复派生（{description[:50]}）")
            raise GuardViolation(f"子智能体 {subtask_id} 同题重复派生，拒绝（防自激循环）")
        if self.count + 1 > self.max_total_subtasks:
            self.violations.append(f"{subtask_id}: 派生总数 {self.count + 1} 超上限 "
                                   f"{self.max_total_subtasks}")
            raise GuardViolation(f"子智能体 {subtask_id} 超出派生总数上限 "
                                 f"（{self.max_total_subtasks}）")
        if key:
            self._seen.add(key)
        self.count += 1
