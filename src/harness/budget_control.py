# -*- coding: utf-8 -*-
"""
harness/budget_control.py —— Budget Manager + Graceful Degradation（102-103）

BudgetManager 同时盯多把尺子：llm 调用次数 / 工具调用 / token / 成本 / 迭代 / 并行度。
degrade_plan() 在接近/超过限额时给出降级动作清单（由 Orchestration 消费）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


class CircuitOpen(RuntimeError):
    """熔断器已打开：连续失败过多，拒绝继续消耗预算。"""


class BudgetExhausted(RuntimeError):
    """迭代预算已用尽，没有剩余步数。"""


@dataclass
class IterationBudget:
    """单次子任务/子运行的迭代预算 + 熔断器（IterationBudget + Circuit Breaker）。

    与 BudgetManager 的分工：BudgetManager 盯整个任务的多把尺子（次数/token/费用）；
    IterationBudget 只管"这一轮子运行还剩几步"，并负责失败连击的熔断——
    连续失败达到阈值就 open，之后所有 consume() 拒绝并抛 CircuitOpen，
    由调用方决定降级（换模式/跳过/如实交付），防止无进展空转烧钱。

    用法：
        budget = IterationBudget(max_iterations=5, max_consecutive_failures=2)
        while budget.check():
            ... 干一步 ...
            budget.consume()
            budget.record_success()   # 或 record_failure()
    """

    max_iterations: int = 5
    max_consecutive_failures: int = 3
    used: int = 0
    failures: int = 0            # 当前连击失败数（成功即清零）
    total_failures: int = 0
    state: str = "closed"        # closed（正常）/ open（熔断）/ half_open（放行试探）

    # ---- 预算 ----
    @property
    def remaining(self) -> int:
        return max(0, self.max_iterations - self.used)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def reserve(self, n: int = 1) -> int:
        """预留 n 步（不超剩余），返回实际预留数；调用后需 consume 结算。"""
        n = max(0, int(n))
        taken = min(n, self.remaining)
        self.used += taken
        return taken

    def release(self, n: int = 1) -> None:
        """把预留但没用掉的步数退回（失败收尾也要退，不能白扣）。"""
        self.used = max(0, self.used - max(0, int(n)))

    def consume(self, n: int = 1) -> None:
        if self.state == "open":
            raise CircuitOpen(f"熔断已打开（连续失败 {self.failures} 次），拒绝继续迭代")
        if self.remaining < max(1, int(n)):
            raise BudgetExhausted(
                f"迭代预算已用尽（{self.used}/{self.max_iterations}），拒绝继续迭代")
        self.used += max(0, int(n))

    def check(self) -> bool:
        """还能不能继续下一步？熔断打开或预算耗尽都返回 False。"""
        return self.state != "open" and not self.exhausted

    # ---- 熔断 ----
    def record_failure(self) -> None:
        self.total_failures += 1
        self.failures += 1
        if self.max_consecutive_failures and \
                self.failures >= self.max_consecutive_failures:
            self.state = "open"

    def record_success(self) -> None:
        self.failures = 0
        if self.state == "half_open":
            self.state = "closed"

    def try_reclose(self) -> bool:
        """open → half_open：调用方可在降级/换路后放一步试探。"""
        if self.state == "open":
            self.state = "half_open"
            return True
        return False

    def snapshot(self) -> dict:
        return {"max_iterations": self.max_iterations, "used": self.used,
                "remaining": self.remaining, "state": self.state,
                "consecutive_failures": self.failures,
                "total_failures": self.total_failures}


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
