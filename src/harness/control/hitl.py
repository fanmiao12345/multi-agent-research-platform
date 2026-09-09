# -*- coding: utf-8 -*-
"""
harness/control/hitl.py —— Human-in-the-loop（DEV_PLAN H8 / 步骤 93-95）

三种 Interrupt：
    approval（高风险工具等批准） / edit（改参数/改产物） / clarification（追问澄清）
flow：Interrupt 产生 → decision_fn(interrupt) 返回 HitlDecision → 据 decision 继续/中止/编辑。

decision_fn 可注入（测试/自动化）或 console 交互（CLI/Web 用）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

APPROVAL = "approval"
EDIT = "edit"
CLARIFICATION = "clarification"
KINDS = (APPROVAL, EDIT, CLARIFICATION)


@dataclass
class HitlDecision:
    action: str                 # approve / reject / edit / clarify
    payload: str = ""           # edit 的新值 / clarify 的问题或答案
    note: str = ""


@dataclass
class Interrupt:
    kind: str
    title: str = ""
    detail: str = ""
    context: dict = field(default_factory=dict)

    def describe(self) -> str:
        return f"[HITL {self.kind}] {self.title}\n{self.detail}"


def resolve(interrupt: Interrupt, decision_fn) -> HitlDecision:
    """把 Interrupt 交给决策者，返回决策（并校验 action 合法）。"""
    decision = decision_fn(interrupt)
    if decision.action not in ("approve", "reject", "edit", "clarify"):
        raise ValueError(f"非法 HITL 决策：{decision.action}")
    return decision


def console_decision(interrupt: Interrupt) -> HitlDecision:
    """CLI 版决策者：打印详情并读一行。"""
    print(interrupt.describe())
    choice = input("[approve/reject/edit/clarify] ? ").strip().lower()
    if choice == "edit":
        return HitlDecision("edit", input("新值: ").strip())
    if choice == "clarify":
        return HitlDecision("clarify", input("澄清内容: ").strip())
    if choice in ("approve", "reject"):
        return HitlDecision(choice)
    raise ValueError(f"未知选择 {choice}")


def run_guarded(interrupt: Interrupt, execute, decision_fn) -> str:
    """高风险动作的标准流程：Interrupt → 决策 → approve 才执行。"""
    decision = resolve(interrupt, decision_fn)
    if decision.action == "reject":
        return "操作被用户拒绝"
    if decision.action == "clarify":
        return f"等待澄清（{decision.payload}），操作未执行"
    if decision.action == "edit":
        return execute(decision.payload)   # 用编辑后的 payload 执行
    return execute()
