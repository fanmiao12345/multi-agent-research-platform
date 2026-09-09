# -*- coding: utf-8 -*-
"""
harness/runtime/agent_runtime.py —— 统一 Agent Runtime（DEV_PLAN A2 / 17，扩展 29-30）

run(task, context) / stream(task, context, on_event)：同一份编排逻辑。
编排链：RuntimeContext → start_run(run.json) → Tracer(trace.jsonl) → Lifecycle
→ LangGraph 循环（Usage 就地累计）→ usage.json → RunOutcome。

demo：python -m src.harness.runtime.agent_runtime
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config.settings import Settings
from src.graph.agent_loop import build_agent_graph
from src.graph.state import new_state
from src.harness.run_store import finish_run, start_run, update_run
from src.harness.runtime import termination
from src.harness.runtime.lifecycle import (COMPLETED, FAILED, CANCELLED, RUNNING,
                                         WAITING_HUMAN, RunRecord)
from src.harness.runtime.run_context import RuntimeContext
from src.harness.tools.executor import ToolExecutor
from src.harness.tools.registry import ToolRegistry
from src.harness.tracer import EV_RUN_END, EV_RUN_START, Tracer
from src.harness.usage import UsageTracker
from src.harness.model_gateway import ACTIVE_JOB, RUN_ID, ROLE, BudgetStop


@dataclass
class RunOutcome:
    """一次 run/stream 的统一返回。"""

    final_text: str
    run_id: str
    thread_id: str
    status: str
    termination_reason: str
    iterations: int
    messages: int
    workspace_dir: str
    usage_path: str = ""
    root_job_id: str = ""


def _merge_state(base: dict, update: dict) -> dict:
    """把一次节点更新合并进运行中的 state（对应 LangGraph 的 reducer 语义）。"""
    out = dict(base)
    for key, value in update.items():
        if key in out and isinstance(out[key], list) and isinstance(value, list):
            out[key] = out[key] + value          # append 型字段（messages 等）
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            merged = dict(out[key])
            merged.update(value)
            out[key] = merged                    # 嵌套 dict 浅合并
        else:
            out[key] = value                     # 标量 last-wins（iteration 等）
    return out


class AgentRuntime:
    """把任何 LLMAdapter 包装成有明确运行规范的 Agent。"""

    def __init__(self, llm, settings: Settings | None = None, *,
                 tool_executor: ToolExecutor | None = None):
        self.llm = llm
        self.settings = settings or Settings()
        self.model_name = getattr(llm, "model_name", "")
        self.tool_executor = tool_executor or ToolExecutor(ToolRegistry.with_builtins())

    # ---- 公共 API ----
    def run_task(self, task: str, context: RuntimeContext | None = None,
                 system_extra: str = "", *, on_event=None, approval_handler=None) -> RunOutcome:
        return self._execute(task, context, streaming=False, system_extra=system_extra,
                             on_event=on_event, approval_handler=approval_handler)

    def stream(self, task: str, context: RuntimeContext | None = None,
               on_event=None, *, approval_handler=None) -> RunOutcome:
        """流式执行：边跑边把节点/LLM/工具事件推给 on_event(ev: dict)。"""
        return self._execute(task, context, streaming=True, on_event=on_event,
                             approval_handler=approval_handler)

    # ---- 内部实现 ----
    def _execute(self, task: str, context: RuntimeContext | None,
                 streaming: bool, on_event=None, system_extra: str = "",
                 approval_handler=None) -> RunOutcome:
        ctx = context or RuntimeContext.from_settings(self.settings)
        ws_root = Path(ctx.workspace_path)      # 兼容 str / Path
        ws_root.mkdir(parents=True, exist_ok=True)

        run = start_run(ws_root, user_input=task, model=self.model_name)
        update_run(run, mode=getattr(self.llm, "run_mode", "custom"),
                   provider=getattr(self.llm, "provider", "unknown"))
        ledger = ACTIVE_JOB.get()
        if ledger:
            update_run(run, root_job_id=ledger.job_id, parent_run_id=RUN_ID.get(), role=ROLE.get())
            ledger.register_run(run["run_id"])
        tracer = Tracer(run["dir"], run_id=run["run_id"], model=self.model_name)
        usage = UsageTracker(model=self.model_name)
        record = RunRecord(run_id=run["run_id"]).transition(RUNNING)
        initial = new_state(task, run_id=run["run_id"],
                            max_iterations=ctx.max_iterations)
        if system_extra:
            # 角色/技能人设注入：作为首条 system 消息进入本轮对话
            initial["messages"] = [{"role": "system", "content": system_extra}
                                   ] + initial["messages"]
        result = initial
        final_text = ""
        reason = termination.UNRECOVERABLE_ERROR
        status = FAILED
        error = None

        def request_approval(spec, arguments):
            record.transition(WAITING_HUMAN)
            update_run(run, status="waiting_human")
            tracer.event("hitl_wait", node="tools", name=spec.name)
            try:
                approved = approval_handler(spec, arguments) is True
                tracer.event("hitl_decision", node="tools", name=spec.name,
                             action="approve" if approved else "reject")
                return approved
            finally:
                record.transition(RUNNING)
                update_run(run, status="running")

        run_token = RUN_ID.set(run["run_id"])
        try:
            tracer.event(EV_RUN_START, node="runtime", user_id=ctx.user_id,
                         model_profile=ctx.model_profile, status="running")
            if on_event:
                on_event({"type": "run_start", "run_id": run["run_id"]})
            app = build_agent_graph(
                self.llm, max_iterations=ctx.max_iterations, tracer=tracer,
                run_id=run["run_id"], tool_executor=self.tool_executor,
                permissions=ctx.permissions, usage=usage, on_event=on_event,
                max_cost=ctx.max_cost,
                approval_handler=request_approval if approval_handler is not None else None)
            graph_config = {"recursion_limit": ctx.max_iterations * 2 + 2}
            if streaming:
                for chunk in app.stream(initial, config=graph_config, stream_mode="updates"):
                    for node, update in chunk.items():
                        result = _merge_state(result, update)
                        if on_event:
                            on_event({"type": "node_end", "node": node,
                                      "state_delta": sorted(update.keys())})
            else:
                result = app.invoke(initial, config=graph_config)
            final_text = result["messages"][-1].get("content") or ""
            reason = result.get("termination_reason") or termination.SUCCESS
            tracer.event("final", node="runtime", content=final_text, reason=reason)
            if on_event:
                on_event({"type": "final", "content": final_text,
                          "reason": reason, "iterations": result.get("iteration", 0)})
            status = CANCELLED if reason == termination.BUDGET_EXCEEDED else COMPLETED
        except BudgetStop as e:
            final_text = str(e)
            reason = termination.BUDGET_EXCEEDED
            status = CANCELLED
        except BaseException as e:
            # 外部异常可能含认证信息；产物只记录类型，原异常仍交给 Python 调用方。
            error = type(e).__name__
            reason = termination.UNRECOVERABLE_ERROR
            status = FAILED if isinstance(e, Exception) else CANCELLED
            if status == CANCELLED:
                reason = termination.HUMAN_STOP
            raise
        finally:
            RUN_ID.reset(run_token)
            record.transition(status, reason)
            run.update(final_text=final_text, termination_reason=reason, error=error,
                       iterations=usage.llm_calls)
            # 终态在产物写完后可见；写入出错时也尝试保存终态，避免永久 running。
            try:
                usage_path = str(usage.write(Path(run["dir"]), run_id=run["run_id"]))
            finally:
                try:
                    tracer.event(EV_RUN_END, node="runtime", status=status, reason=reason,
                                 error=error, iterations=usage.llm_calls,
                                 messages=len(result["messages"]))
                finally:
                    finish_run(run, status.lower())

        return RunOutcome(
            final_text=final_text, run_id=run["run_id"], thread_id=record.thread_id,
            status=record.status, termination_reason=reason,
            iterations=result.get("iteration", 0),
            messages=len(result["messages"]), workspace_dir=run["dir"],
            usage_path=usage_path, root_job_id=ledger.job_id if ledger else "")


def main() -> None:
    from src.llm.mock import MockLLM

    runtime = AgentRuntime(MockLLM())
    outcome = runtime.run_task("计算 27*43，然后告诉我结果",
                               RuntimeContext.from_settings(max_iterations=4))
    print(f"outcome  = {outcome}")
    print(f"最终回答 = {outcome.final_text}")
    print(f"终止原因 = {outcome.termination_reason}")
    print(f"usage    = {outcome.usage_path}")


if __name__ == "__main__":
    main()
