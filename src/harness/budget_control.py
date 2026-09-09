# -*- coding: utf-8 -*-
"""
harness/budget_control.py —— Budget Manager + Graceful Degradation（102-103）

BudgetManager 同时盯多把尺子：llm 调用次数 / 工具调用 / token / 成本 / 迭代 / 并行度。
degrade_plan() 在接近/超过限额时给出降级动作清单（由 Orchestration 消费）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BudgetState:
    llm_calls: int = 0
    tool_calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    iterations: int = 0


@dataclass
class BudgetManager:
    max_llm_calls: int | None = 100
    max_tool_calls: int | None = 50
    max_tokens: int | None = 500_000
    max_cost_usd: float | None = 0.5
    max_iterations: int | None = 20
    max_parallelism: int = 3
    state: BudgetState = field(default_factory=BudgetState)

    def record_llm(self, prompt: int = 0, completion: int = 0,
                   cost: float = 0.0, iterations: int = 0) -> None:
        self.state.llm_calls += 1
        self.state.tokens += prompt + completion
        self.state.cost_usd += cost
        self.state.iterations += iterations

    def record_tool(self, n: int = 1) -> None:
        self.state.tool_calls += n

    # ---- 超限判断（返回 True 表示已超）----
    @property
    def over_llm(self) -> bool:
        return self.max_llm_calls is not None and self.state.llm_calls >= self.max_llm_calls

    @property
    def over_tokens(self) -> bool:
        return self.max_tokens is not None and self.state.tokens >= self.max_tokens

    @property
    def over_cost(self) -> bool:
        return self.max_cost_usd is not None and self.state.cost_usd >= self.max_cost_usd

    @property
    def exhausted(self) -> bool:
        return self.over_llm or self.over_tokens or self.over_cost


def degrade_plan(budget: BudgetManager, *, context_compression: bool = False) -> list[str]:
    """预算吃紧时建议的降级动作（顺序即优先级，由上层逐步采用）。"""
    actions: list[str] = []
    st = budget.state
    if budget.over_cost:
        actions.append("切换 cheap 模型")
    if budget.over_tokens or (budget.max_tokens and st.tokens > budget.max_tokens * 0.7):
        actions.append("压缩 Context")
    if budget.over_llm or (budget.max_llm_calls and st.llm_calls > budget.max_llm_calls * 0.7):
        actions.append("减少 Worker / 跳过 Debate")
    if budget.max_iterations and st.iterations >= budget.max_iterations * 0.7:
        actions.append("降低 Review 轮数")
    if budget.max_parallelism == 1:
        actions.append("取消低优先级 Task")
    if context_compression and "压缩 Context" in actions:
        pass
    return actions or ["无需降级"]
