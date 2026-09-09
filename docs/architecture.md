# 架构说明（architecture）

## 分层

```
┌─────────────────────────────────────────────────────────┐
│ 应用层：CLI（-m 各模块） / Web Workbench（interfaces/web）│
├─────────────────────────────────────────────────────────┤
│ Orchestration（多 Agent 策略，同一套原语）               │
│   pipeline · manager_worker · fanout · debate           │
│   · dynamic_team · single                              │
├─────────────────────────────────────────────────────────┤
│ Harness 能力层                                          │
│   runtime   lifecycle · run_context · agent_runtime     │
│   graph     agent_loop（LangGraph：State/Edge/条件路由） │
│   context   builder · policy · budget · compressors     │
│   tools     registry · executor · policy(风险/权限)      │
│   skills    registry · recall(BM25) · router            │
│   memory    checkpointer · long_term · knowledge        │
│   planning  planner · task_graph · scheduler · replanner│
│   control   errors · retry/timeout · loop_guard ·       │
│             guardrails · hitl · idempotency             │
│   durable   断点续跑（plan.json 每步落盘）                │
│   mcp       零依赖 JSON-RPC Client/Server               │
│   models    profiles · router · factory                 │
│   accounting/usage/tracer（可观测三件套）                 │
├─────────────────────────────────────────────────────────┤
│ LangGraph Runtime（langgraph：checkpoint/thread/stream） │
├─────────────────────────────────────────────────────────┤
│ LLM Adapter（Mock / OpenAI 兼容）· 内置工具 · MCP 远端    │
└─────────────────────────────────────────────────────────┘
```

## 一次运行的数据流（AgentRuntime.run_task）

```
RuntimeContext(静态参数) → start_run(run.json)
→ Tracer(trace.jsonl) → Lifecycle(CREATED→RUNNING)
→ build_agent_graph(闭包注入 llm/executor/permissions/usage/on_event)
→ LangGraph 循环：agent_node ⇄ tools_node（Executor：schema→权限→风险→
   重试→超时→执行→结果处理）→ should_continue 决定 END / tools / timeout
→ usage.json（AccountingLedger 多维记账）→ RunOutcome
```

产物约定：每次运行 = workspaces/<run_id>/{run.json, trace.jsonl, usage.json,
plan.json(如用规划), *.md 工件}——Web/评测/断点续跑都只读这份磁盘真相。

## 关键设计决策（对应计划文档 ADR）

- D-001：核心运行时用 LangGraph，不重复造轮子
- D-003：Mock First——所有评测离线可复现
- D-004：LangGraph 管"图怎么跑"，Harness 管"跑什么/给谁/看什么/允许什么/何时停"
- D-005：RAG / Memory / Context 三个模块严格分离
- D-006：本地 Trace 优先（run.json/trace.jsonl/usage.json 不依赖任何外部服务）
- D-007：Multi-Agent 是 Policy，不是多个 Runtime
