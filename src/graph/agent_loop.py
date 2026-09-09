# -*- coding: utf-8 -*-
"""
graph/agent_loop.py —— 最小 Tool Calling Loop（DEV_PLAN A0.6/A0.7：步骤 12/13）

Graph 结构（条件路由）：

    START
      ↓
    agent ──(无工具请求)──▶ END
      │
   (请求工具，未超限)
      ↓
    tools ──────────────▶ agent
      │
   (请求工具，但已达 max_iterations) ──▶ timeout ──▶ END

学习点：ReAct / Tool Calling / Conditional Routing / Agent Loop / Termination。

demo：python -m src.graph.agent_loop
"""

from __future__ import annotations

import json
import time

from langgraph.graph import END, START, StateGraph

from src.builtin_tools import TOOL_SCHEMAS
from src.graph.state import AgentState
from src.harness.tools.executor import ToolExecutor
from src.harness.tools.registry import ToolRegistry
from src.llm.base import LLMAdapter
from src.harness.model_gateway import model_call, check_root_budget


def _assistant_payload(reply) -> dict:
    """把 Adapter 的 ChatResult 转成 OpenAI 风格 assistant 消息。"""
    msg = {"role": "assistant", "content": reply.content}
    if reply.tool_calls:
        msg["tool_calls"] = [
            {"id": c.id, "type": "function",
             "function": {"name": c.name, "arguments": json.dumps(
                 c.arguments, ensure_ascii=False)}}
            for c in reply.tool_calls
        ]
    return msg


def build_agent_graph(llm: LLMAdapter, max_iterations: int = 5,
                      tracer=None, run_id: str = "",
                      tool_executor: ToolExecutor | None = None,
                      permissions: set | frozenset | None = None,
                      usage=None, on_event=None, checkpointer=None,
                      max_cost=None, approval_handler=None) -> object:
    """用闭包把 llm / 限制 / tracer / 工具执行器 / 用量统计 / 事件回调绑进 graph。

    工具执行默认走 ToolExecutor（ToolRegistry 内置两工具）；传 permissions 后
    Executor 会做权限检查（RuntimeContext.permissions 接入点，步骤 24）。
    usage：UsageTracker（步骤 29）；on_event：实时事件回调（步骤 30 streaming）。
    checkpointer：LangGraph Checkpointer（步骤 66）——同一 thread 跨调用保持 State。
    """
    executor = tool_executor or ToolExecutor(ToolRegistry.with_builtins())
    schemas = (executor.registry.to_openai_tools() if tool_executor
               else TOOL_SCHEMAS)

    def budget_stop(iteration: int) -> dict:
        if tracer:
            tracer.event("loop_guard", node="agent", reason="budget_exceeded")
        return {"messages": [{"role": "assistant", "content": "（已达估算预算上限，任务已停止）"}],
                "iteration": iteration, "termination_reason": "budget_exceeded"}

    def agent_node(state: AgentState) -> dict:
        if usage is not None and usage.cost_limit_reached(max_cost):
            return budget_stop(state.get("iteration", 0))
        t0 = time.perf_counter()
        reply = model_call(llm, state["messages"], tools=schemas)
        latency = time.perf_counter() - t0
        iteration = state.get("iteration", 0) + 1
        if usage is not None:
            usage.record(getattr(llm, "model_name", ""), reply.usage, latency)
        check_root_budget()
        if tracer:
            tracer.event("llm_call", node="agent",
                         model=getattr(llm, "model_name", ""), round=iteration,
                         tool_request=[c.name for c in reply.tool_calls],
                         latency=latency,
                         input_tokens=(reply.usage or {}).get("prompt_tokens"),
                         output_tokens=(reply.usage or {}).get("completion_tokens"))
        if on_event:
            on_event({"type": "llm", "round": iteration, "model": getattr(llm, "model_name", ""),
                      "content": reply.content,
                      "tool_requests": [c.name for c in reply.tool_calls],
                      "latency": round(latency, 4)})
        if usage is not None and usage.cost_limit_reached(max_cost):
            return budget_stop(iteration)
        return {"messages": [_assistant_payload(reply)], "iteration": iteration}

    def tools_node(state: AgentState) -> dict:
        # 执行上一条 assistant 消息里请求的全部工具，结果作为 role=tool 消息回填
        tool_msgs = []
        for m in reversed(state["messages"]):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    check_root_budget(new_call=True)
                    name = tc["function"]["name"]
                    args = tc["function"].get("arguments") or "{}"
                    try:
                        arguments = json.loads(args) if isinstance(args, str) else args
                    except json.JSONDecodeError:
                        arguments = {}
                    t0 = time.perf_counter()
                    result = executor.execute(name, arguments, permissions=permissions,
                                              approval_handler=approval_handler)
                    check_root_budget()
                    latency = time.perf_counter() - t0
                    tool_msgs.append({"role": "tool", "tool_call_id": tc["id"],
                                      "content": result})
                    if tracer:
                        tracer.event("tool_call", node="tools",
                                     name=name, arguments=arguments,
                                     latency=latency)
                        tracer.event("tool_result", node="tools",
                                     name=name, result=result[:200])
                    if on_event:
                        on_event({"type": "tool", "name": name, "arguments": arguments,
                                  "result": result[:300], "latency": round(latency, 4)})
                break  # 只回应当前这一轮
        if tracer:
            tracer.event("state_update", node="tools",
                         added_tool_messages=len(tool_msgs))
        return {"messages": tool_msgs}

    def timeout_node(state: AgentState) -> dict:
        note = {"role": "assistant",
                "content": f"（已达最大迭代限制 {state.get('max_iterations')} 轮，任务未完成）"}
        if tracer:
            tracer.event("loop_guard", node="timeout", reason="max_iterations")
        return {"messages": [note], "termination_reason": "max_iterations"}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        wants = bool(last.get("tool_calls"))
        if not wants:
            return "end"
        if state.get("iteration", 0) >= state.get("max_iterations", 5):
            return "timeout"
        return "tools"

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_node("timeout", timeout_node)
    graph.add_edge(START, "agent")
    graph.add_edge("tools", "agent")
    graph.add_edge("timeout", END)
    graph.add_conditional_edges(
        "agent", should_continue, {"tools": "tools", "timeout": "timeout", "end": END})
    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)   # 66：thread 持久化
    return graph.compile()


def main() -> None:
    """demo：先用 MockLLM 验证“工具调用 → 循环终止”整条链路。"""
    from src.harness.run_store import finish_run, start_run
    from src.harness.tracer import Tracer
    from src.llm.mock import MockLLM

    question = "计算 27*43，然后告诉我结果"
    run = start_run(user_input=question, model="mock-rule-v1")
    tracer = Tracer(run["dir"])
    app = build_agent_graph(MockLLM(), max_iterations=5, tracer=tracer,
                            run_id=run["run_id"])
    result = app.invoke({"messages": [{"role": "user", "content": question}],
                         "iteration": 0, "max_iterations": 5})
    final = result["messages"][-1]["content"]
    print(f"run_id = {run['run_id']}")
    print(f"轮数   = {result['iteration']}")
    print(f"最终   = {final}")
    finish_run(run, "completed")
    print(f"产物   = {run['dir']}")


if __name__ == "__main__":
    main()
