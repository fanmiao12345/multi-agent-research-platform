# -*- coding: utf-8 -*-
"""
orchestration/debate.py —— Debate-lite 策略（步骤 81）

仅当"证据冲突 / 高不确定 / 关键决策"才使用（调用方决定触发）。
流程：Pro Agent 与 Con Agent 各自引据辩护 → Judge（LLM 裁决）汇总。
"""

from __future__ import annotations

from functools import partial
from src.harness.model_gateway import model_call, BudgetStop

call_model = partial(model_call, purpose="judge", role="judge")

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from src.orchestration.base import StrategyResult, Worker, pack_card


def run_debate(question: str, worker: Worker, llm,
               pro_side: str = "支持方", con_side: str = "反对方",
               name: str = "debate") -> StrategyResult:
    def argue(side: str, stance: str) -> str:
        return worker(pack_card(question, f"辩论·{side}", None) +
                      f"立场：{stance}。请给出有依据的论点与反驳预期。", side)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_pro = pool.submit(copy_context().run, argue, "pro", pro_side)
        f_con = pool.submit(copy_context().run, argue, "con", con_side)
        pro, con = f_pro.result(), f_con.result()

    judge = call_model(llm, [{"role": "user",
                       "content": f"你是裁决者。问题：{question}\n\n【支持方】\n{pro}\n\n"
                                  f"【反对方】\n{con}\n\n请裁决并说明理由。"}],
                     tools=[])
    verdict = (judge.content or "").strip() or "（裁决者未输出）"
    final = (f"【{pro_side}】\n{pro}\n\n【{con_side}】\n{con}\n\n"
             f"【裁决】\n{verdict}")
    return StrategyResult(name=name, final=final, worker_calls=2,
                          stages=[{"side": "pro"}, {"side": "con"},
                                  {"judge": verdict[:100]}])
