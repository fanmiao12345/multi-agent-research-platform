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
from src.harness.context.builder import compose_context
from src.harness.context.policy import ContextSource
from src.harness.memory.checkpointer import make_checkpointer, thread_config
from src.harness.memory.knowledge import KNOWLEDGE_DIR, retrieve as retrieve_knowledge
from src.harness.memory.long_term import KIND_SEMANTIC, LongTermStore
from src.harness.memory.policy import should_write
from src.harness.run_store import finish_run, start_run, update_run, write_json
from src.harness.runtime import termination
from src.harness.runtime.lifecycle import (COMPLETED, FAILED, CANCELLED, RUNNING,
                                         WAITING_HUMAN, RunRecord)
from src.harness.runtime.run_context import RuntimeContext
from src.harness.tools.executor import ToolExecutor
from src.harness.tools.registry import ToolRegistry
from src.harness.tracer import EV_RUN_END, EV_RUN_START, Tracer
from src.harness.usage import UsageTracker
from src.harness.model_gateway import ACTIVE_JOB, RUN_ID, ROLE, BudgetStop
from src.harness.skills.registry import SkillRegistry
from src.harness.skills.router import inject, route
from src.harness.tools.subagent import subagent_permission_scope


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
                 tool_executor: ToolExecutor | None = None,
                 skill_registry: SkillRegistry | None = None,
                 memory_store: LongTermStore | None = None,
                 knowledge_root=None):
        self.llm = llm
        self.settings = settings or Settings()
        self.model_name = getattr(llm, "model_name", "")
        self.tool_executor = tool_executor or ToolExecutor(ToolRegistry.with_builtins())
        self.skill_registry = skill_registry or SkillRegistry()
        self.memory_store = memory_store or LongTermStore(
            self.settings.workspace_dir / "memory_store.json")
        self.knowledge_root = knowledge_root or KNOWLEDGE_DIR
        self._checkpointer = make_checkpointer()

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

    @staticmethod
    def _history_without_current(messages: list[dict], task: str) -> list[dict]:
        """去掉 system 与最新用户问题；其余消息交给 Context Builder 限窗。"""
        history = [m for m in messages if m.get("role") != "system"]
        for index in range(len(history) - 1, -1, -1):
            msg = history[index]
            if msg.get("role") == "user" and (msg.get("content") or "") == task:
                return history[:index] + history[index + 1:]
        return history

    @staticmethod
    def _explicit_memory(task: str) -> tuple[bool, str]:
        for marker in ("请记住", "记住", "以后都", "今后都"):
            if marker in task:
                content = task.split(marker, 1)[1].strip(" ：:，,。")
                if content:
                    return True, content
        return False, ""

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
        context_records: list[dict] = []

        skill_decision = {"skill": None, "candidates": [], "reason": "技能未启用"}
        selected_skill = None
        if ctx.skills_enabled:
            skill_decision = route(self.skill_registry, self.llm, task,
                                   top_k=5, allow_llm=False)
            if skill_decision.get("skill"):
                selected_skill = self.skill_registry.get(skill_decision["skill"])
            tracer.event("skill_route", node="context",
                         selected=skill_decision.get("skill"),
                         reason=skill_decision.get("reason", ""),
                         candidates=[c.get("name") for c in skill_decision.get("candidates", [])])

        effective_permissions = frozenset(ctx.permissions)
        if selected_skill and selected_skill.allowed_tools:
            effective_permissions = effective_permissions.intersection(selected_skill.allowed_tools)

        explicit, explicit_text = self._explicit_memory(task)
        if explicit and ctx.memory_enabled:
            allowed, policy_reason = should_write(KIND_SEMANTIC, explicit=True)
            if allowed:
                self.memory_store.remember(
                    KIND_SEMANTIC, explicit_text,
                    source=f"explicit:{run['run_id']}", confidence=1.0)
                tracer.event("memory_write", node="context", kind=KIND_SEMANTIC,
                             reason=policy_reason, source=f"explicit:{run['run_id']}")
        memories = (self.memory_store.search(task, top_k=3)
                    if ctx.memory_enabled else [])
        knowledge = (retrieve_knowledge(task, root=self.knowledge_root, top_k=3)
                     if ctx.knowledge_enabled else [])

        sources: list[ContextSource] = []
        if system_extra:
            sources.append(ContextSource(kind="instructions", content=system_extra))
        if selected_skill:
            sources.append(ContextSource(kind="skill", content=inject(selected_skill),
                                         meta={"name": selected_skill.name,
                                               "allowed_tools": selected_skill.allowed_tools}))
        if memories:
            sources.append(ContextSource(
                kind="memory", content="\n".join(
                    f"- [{m.id}] {m.content}（来源：{m.source or '未标注'}）" for m in memories),
                policy="RETRIEVE_IF_RELEVANT"))
        if knowledge:
            sources.append(ContextSource(
                kind="evidence", content="\n".join(
                    f"- [{item['name']}] {item['snippet']}" for item in knowledge),
                policy="RETRIEVE_IF_RELEVANT"))
        if ctx.handoff_text:
            sources.append(ContextSource(kind="handoff", content=ctx.handoff_text))
        if effective_permissions:
            sources.append(ContextSource(kind="tools", content="本次允许工具：" +
                                         "、".join(sorted(effective_permissions))))

        def compose_for_call(state):
            history = self._history_without_current(
                state.get("messages", []), state.get("user_task") or task)
            continuing_tool = bool(history and history[-1].get("role") == "tool")
            messages, stats = compose_context(
                state.get("user_task") or task, sources, history=history,
                total_budget=ctx.context_budget,
                append_question=not continuing_tool)
            context_records.append({
                "round": len(context_records) + 1,
                "skill": skill_decision.get("skill"),
                "skill_reason": skill_decision.get("reason", ""),
                "memory_ids": [m.id for m in memories],
                "knowledge": [item["name"] for item in knowledge],
                "stats": stats,
            })
            return messages, stats

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
                permissions=effective_permissions, usage=usage, on_event=on_event,
                max_cost=ctx.max_cost,
                approval_handler=request_approval if approval_handler is not None else None,
                context_composer=compose_for_call,
                tool_allowlist=tuple(effective_permissions),
                checkpointer=(self._checkpointer if ctx.thread_id else None))
            graph_config = {"recursion_limit": ctx.max_iterations * 2 + 2}
            if ctx.thread_id:
                graph_config.update(thread_config(ctx.thread_id))
            with subagent_permission_scope(effective_permissions):
                if streaming:
                    for chunk in app.stream(initial, config=graph_config,
                                            stream_mode="updates"):
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
            if context_records:
                write_json(Path(run["dir"]) / "context.json", {
                    "schema_version": 1, "run_id": run["run_id"],
                    "thread_id": ctx.thread_id or None,
                    "skill": skill_decision, "records": context_records,
                    "memory_ids": [m.id for m in memories],
                    "knowledge": [item["name"] for item in knowledge],
                })
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
