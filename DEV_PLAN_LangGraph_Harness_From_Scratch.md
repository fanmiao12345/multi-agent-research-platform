# agent-mvp：从零搭建 LangGraph Agent Harness & Multi-Agent 系统

> **项目目标**：从一个空目录开始，基于 LangGraph 搭建一套完整、可运行、可评测、可恢复、可扩展的 Agent Harness，并在其上实现多种 Multi-Agent Orchestration。
>
> **学习目标**：系统覆盖 Agent Loop、ReAct、Tool Calling、Skills、Planning、Context Engineering、Memory、Multi-Agent、Human-in-the-loop、Durable Execution、Guardrails、Evaluation、Tracing、MCP 等核心知识。
>
> **工程目标**：最终不是“做很多 Agent Demo”，而是形成一套统一的 Agent Harness。不同 Agent、Tool 和多智能体模式只是运行在 Harness 上的不同策略与组件。
>
> 📌 **执行状态**：里程碑 A0/M1~M12 的代码与测试均已落地，落地对照与已知缺口见
> [`docs/EXECUTION_STATUS.md`](docs/EXECUTION_STATUS.md)（含离线评测基线，最近更新 2026-09-07）。

---

# 0. 项目定位与边界

## 0.1 最终定位

项目定位为：

**一个基于 LangGraph Runtime 的轻量 Agent Harness + Multi-Agent Runtime。**

整体分层：

```text
Application / CLI / Web Workbench
              ↓
Multi-Agent Orchestration
              ↓
Agent Harness
              ↓
LangGraph Runtime
              ↓
LLM / Tools / MCP / External Services
```

其中：

- **LangGraph** 解决 Graph 如何执行、暂停、恢复、持久化和并行。
- **Harness** 解决 Agent 应该如何运行、看到什么上下文、调用什么工具、如何恢复、何时停止、如何评测。
- **Multi-Agent Orchestration** 解决多个 Agent 如何分工、协作、交接、并行与审核。

---

## 0.2 LangGraph 负责什么

直接使用 LangGraph 提供：

- StateGraph
- Node / Edge
- Conditional Edge
- State / Reducer
- Command
- Send
- Subgraph
- Checkpointer
- Persistence
- Interrupt / Resume
- Streaming
- Durable Execution

这些底层能力不重复造轮子。

---

## 0.3 本项目重点自己实现什么

### Agent Harness

- Agent 生命周期
- Agent Loop 约定
- Run Context
- Termination Policy
- Planner / Scheduler / Replanner
- Tool Registry / Tool Executor / Tool Policy
- Skill Registry / Skill Router
- Context Builder / Selector / Compressor
- Memory Policy
- Workspace / Artifact
- Retry / Timeout / Loop Guard
- Guardrail / Approval
- Model Routing
- Budget Control
- Usage Accounting
- Trace / Metrics
- Eval / Benchmark

### Multi-Agent

- Role-based
- Pipeline
- Manager–Worker
- Fan-out / Fan-in
- Handoff
- Blackboard
- Generator–Critic
- Debate-lite
- Dynamic Team
- Subagent as Tool

### Agent Ecosystem

- MCP Client
- MCP Server
- 可选 A2A 实验
- Web Agent Workbench

---

## 0.4 明确不做什么

- 不重新实现 LangGraph 已提供的底层 checkpoint runtime。
- 不为了“Agent 数量多”创建大量角色。
- 不让每一种 Multi-Agent Pattern 各写一套独立 Runtime。
- 不让所有 Agent 默认共享全部 conversation。
- 不把 RAG、Memory、Context 混成一个模块。
- 不在没有 Eval 的情况下凭主观感觉判断优化效果。
- 不在核心 Runtime 尚未稳定时优先做复杂 UI。

---

# 1. 从零开始：项目初始化

> 本阶段假设当前只有一个空目录，不依赖任何旧代码。
>
> 第一目标不是“马上做多 Agent”，而是先建立一个结构正确、可测试、可观察的最小 LangGraph Agent。

## A0.1 创建项目

建议目录：

```text
agent-mvp/
```

初始化 Git：

```bash
git init
```

创建基础文件：

```text
agent-mvp/
├── README.md
├── DEV_PLAN.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── src/
├── tests/
├── eval/
├── workspaces/
├── docs/
└── config/
```

### 完成标准

- 项目能够独立 Git 管理。
- `.env`、workspace 等敏感/运行文件不会提交。
- README 可以说明如何安装和启动。

---

## A0.2 创建 Python 环境

推荐：

```text
Python 3.11+
```

创建虚拟环境：

```bash
python -m venv .venv
```

安装第一批依赖：

```text
langgraph
langchain-core
模型 Provider Adapter
pytest
python-dotenv
```

Provider Adapter 根据实际模型决定，例如 OpenAI-compatible、OpenAI、Anthropic 等。

### 完成标准

```bash
python -c "import langgraph"
```

可以正常运行。

---

## A0.3 建立配置系统

先不要把配置散落在源码中。

建立：

```text
config/
  settings.py

.env
.env.example
```

第一版支持：

```text
MODEL_PROVIDER
MODEL_NAME
MODEL_API_KEY
MODEL_BASE_URL
TEMPERATURE
MAX_TOKENS
TRACE_LEVEL
WORKSPACE_DIR
```

要求：

- API Key 只从环境读取。
- `.env.example` 只放字段，不放真实 Key。
- Mock 模式可以在没有 API Key 时运行。

---

## A0.4 第一个 LangGraph：Hello Graph

先不接 LLM。

建立：

```text
src/graph/hello_graph.py
```

实现最简单 Graph：

```text
START
  ↓
hello_node
  ↓
END
```

State：

```python
class HelloState(TypedDict):
    input: str
    output: str
```

### 学习点

- StateGraph
- State
- Node
- Edge
- compile
- invoke

### 完成标准

```bash
python -m src.graph.hello_graph
```

输出确定结果。

---

## A0.5 第一个 LLM Node

新增：

```text
src/llm/
  base.py
  mock.py
  provider.py
```

定义统一接口：

```text
LLMAdapter
 ├── MockLLM
 └── RealLLM
```

Graph：

```text
START
  ↓
llm_node
  ↓
END
```

先跑 Mock，再跑真实模型。

### 完成标准

同一个 Graph 能通过配置切换：

```text
mock
real
```

而不修改 Graph 结构。

---

## A0.6 第一个 Tool Calling Loop

先做两个简单工具：

```text
calculator
current_time
```

Graph：

```text
START
  ↓
Agent
  ↓
是否需要 Tool？
 ├─ No → END
 └─ Yes
      ↓
     Tool
      ↓
     Agent
```

### 学习点

- ReAct
- Tool Calling
- Conditional Routing
- Agent Loop
- Termination

### 完成标准

Agent 能完成：

```text
“计算 27 * 43，然后告诉我结果。”
```

并且真实发生 Tool Call，而不是模型自行计算。

---

## A0.7 第一套自动化测试

建立：

```text
tests/
  test_graph.py
  test_llm_mock.py
  test_tools.py
  test_agent_loop.py
```

第一批测试不依赖真实 API。

至少覆盖：

```text
Graph 可编译
MockLLM 可运行
Tool 参数正确
Tool 结果正确
Agent Loop 可以结束
最大循环可以阻止死循环
```

### 完成标准

```bash
pytest
```

全部通过。

---

## A0.8 第一版 Run Workspace

每次正式运行创建：

```text
workspaces/<run_id>/
```

第一版至少保存：

```text
run.json
trace.jsonl
artifacts/
```

`run.json`：

```text
run_id
started_at
finished_at
status
input
model
```

### 完成标准

每运行一次 Agent，都能找到对应 Run 目录。

---

## 阶段 A0 最终验收

从一个空目录最终跑通：

```text
用户输入
   ↓
LangGraph
   ↓
LLM
   ↓
Tool Call
   ↓
Tool Result
   ↓
LLM
   ↓
Final Answer
```

并同时拥有：

```text
配置
测试
Trace
Workspace
MockLLM
真实 LLM
```

**只有 A0 完成后，才进入真正的 Harness 开发。**

---

# 2. 最终工程目录

不要求第一天就创建全部空文件。目录随着阶段逐步出现。

最终建议：

```text
agent-mvp/
│
├── src/
│   ├── graph/
│   │   ├── state.py
│   │   ├── reducers.py
│   │   ├── routes.py
│   │   └── main_graph.py
│   │
│   ├── harness/
│   │   ├── runtime/
│   │   │   ├── lifecycle.py
│   │   │   ├── run_context.py
│   │   │   └── termination.py
│   │   │
│   │   ├── planning/
│   │   │   ├── planner.py
│   │   │   ├── task.py
│   │   │   ├── task_graph.py
│   │   │   ├── scheduler.py
│   │   │   └── replanner.py
│   │   │
│   │   ├── context/
│   │   │   ├── builder.py
│   │   │   ├── selector.py
│   │   │   ├── compressor.py
│   │   │   ├── budget.py
│   │   │   └── handoff.py
│   │   │
│   │   ├── memory/
│   │   │   ├── short_term.py
│   │   │   ├── long_term.py
│   │   │   ├── semantic.py
│   │   │   ├── episodic.py
│   │   │   ├── procedural.py
│   │   │   └── retrieval.py
│   │   │
│   │   ├── tools/
│   │   │   ├── registry.py
│   │   │   ├── executor.py
│   │   │   ├── schema.py
│   │   │   ├── policy.py
│   │   │   ├── result_processor.py
│   │   │   └── mcp.py
│   │   │
│   │   ├── control/
│   │   │   ├── budget.py
│   │   │   ├── retry.py
│   │   │   ├── timeout.py
│   │   │   ├── guardrail.py
│   │   │   ├── approval.py
│   │   │   └── loop_guard.py
│   │   │
│   │   ├── workspace/
│   │   │   ├── workspace.py
│   │   │   ├── artifacts.py
│   │   │   └── run_store.py
│   │   │
│   │   └── tracing/
│   │       ├── tracer.py
│   │       ├── events.py
│   │       └── metrics.py
│   │
│   ├── agents/
│   │   ├── base.py
│   │   ├── researcher.py
│   │   ├── organizer.py
│   │   ├── writer.py
│   │   ├── reviewer.py
│   │   └── profiles.py
│   │
│   ├── orchestration/
│   │   ├── base.py
│   │   ├── pipeline.py
│   │   ├── manager_worker.py
│   │   ├── fanout.py
│   │   ├── handoff.py
│   │   ├── debate.py
│   │   └── dynamic_team.py
│   │
│   ├── skills/
│   ├── builtin_tools/
│   ├── llm/
│   └── interfaces/
│       ├── cli/
│       └── web/
│
├── eval/
│   ├── datasets/
│   ├── evaluators/
│   ├── benchmark.py
│   ├── trajectory.py
│   └── report.py
│
├── tests/
├── workspaces/
├── docs/
├── config/
├── README.md
├── DEV_PLAN.md
└── pyproject.toml
```

---

# 3. 核心状态模型

不要把所有内容都塞进 `messages`。

整个 Harness 明确区分：

```text
Graph State
Runtime Context
Long-term Store
Workspace / Artifacts
```

---

## 3.1 Graph State

Graph State 只保存当前 Thread 中需要参与流程控制的数据。

第一版建议：

```python
class AgentState(TypedDict):
    run_id: str
    messages: list

    user_task: str
    task_type: str

    plan: list
    current_task_id: str | None
    completed_task_ids: list[str]
    failed_task_ids: list[str]

    active_agent: str | None
    agent_outputs: list

    evidence: list
    artifacts: list
    tool_events: list

    iteration: int
    status: str
    errors: list

    metrics: dict
```

并行写字段必须定义 Reducer：

```text
agent_outputs
evidence
artifacts
tool_events
errors
```

---

## 3.2 Runtime Context

保存一次 Run 的静态运行参数，例如：

```text
user_id
model_profile
max_iterations
max_cost
permissions
workspace_path
trace_level
```

这些数据不应该作为对话消息反复传给模型。

---

## 3.3 Long-term Store

跨 Thread 保存：

```text
用户偏好
长期知识
任务经验
已确认事实
Skill 使用经验
历史任务摘要
```

---

## 3.4 Workspace

Workspace 保存任务中间产物：

```text
facts
evidence
notes
artifacts
decisions
open_questions
errors
final_output
```

Workspace 与聊天消息分开。

---

# 4. 阶段 A：Single-Agent Harness

目标：从“能跑一个 Graph”升级为“有明确运行规范的 Agent Runtime”。

## A1. Agent Lifecycle

定义统一生命周期：

```text
CREATED
  ↓
RUNNING
  ↓
WAITING_TOOL / WAITING_HUMAN
  ↓
RUNNING
  ↓
COMPLETED / FAILED / CANCELLED
```

记录：

```text
run_id
thread_id
status
started_at
ended_at
termination_reason
```

---

## A2. 统一 Agent Runtime

所有 Agent 共享同一套运行接口，例如：

```text
run(task, context)
resume(run_id)
stream(task, context)
```

Agent 本身只描述：

```text
prompt
model_profile
skills
tools
permissions
output_schema
```

不要让每个 Agent 自己重新实现 Tool Loop、Trace、Retry。

---

## A3. Structured Output

重要内部节点尽可能使用结构化输出：

```text
Planner Output
Tool Decision
Handoff Decision
Review Result
Memory Write
Final Evaluation
```

避免 Agent 之间靠自然语言猜字段。

---

## A4. Termination Policy

统一定义：

```text
success
max_iterations
budget_exceeded
repeated_failure
human_stop
unrecoverable_error
```

---

## A5. Streaming

至少支持：

```text
LLM token
Node Start / End
Tool Start / End
State Update
Interrupt
Final
```

### 阶段验收

同一个 Agent 可以：

```text
invoke
stream
resume
```

并且所有行为进入统一 Trace 和 Workspace。

---

# 5. 阶段 B：Eval + Trace 先行

> 在 Planning、Memory、Multi-Agent 之前先建立“尺子”。

## B1. Trace Event Schema

每个关键事件记录：

```text
run_id
thread_id
event_id
parent_event_id
node
agent
task_id
model
timestamp
latency
input_tokens
output_tokens
tool_calls
state_delta
status
error
```

输出：

```text
workspaces/<run_id>/trace.jsonl
```

---

## B2. 第一版 Benchmark Dataset

先建立 15~20 个任务，覆盖：

```text
简单问答
单工具任务
错误工具诱导任务
多工具任务
搜索任务
长文本任务
多步骤任务
需要 Planning 的任务
并行任务
冲突证据任务
工具失败任务
需要人工确认任务
```

---

## B3. Component Metrics

至少：

```text
Tool Selection Accuracy
Tool Argument Accuracy
Routing Accuracy
Structured Output Valid Rate
Memory Retrieval Precision
Context Selection Precision
```

---

## B4. Runtime Metrics

记录：

```text
LLM Calls
Tool Calls
Retry Count
Iterations
Latency
Input Tokens
Output Tokens
Estimated Cost
```

---

## B5. End-to-End Metrics

至少：

```text
Task Success Rate
Plan Completion Rate
Final Quality Score
Failure Rate
Human Intervention Rate
```

---

## B6. Trajectory Eval

评价执行过程：

```text
是否正确拆任务？
是否调用正确工具？
是否重复调用？
是否错误 Handoff？
是否无意义循环？
是否在证据不足时直接回答？
失败以后有没有采取正确恢复策略？
```

---

## B7. 可选 LangSmith Adapter

本地 Trace 必须完整可用。

LangSmith 只作为：

```text
Optional Observability Adapter
```

### 阶段验收

每次 Benchmark 能输出：

```text
benchmark_report.json
benchmark_report.md
```

并可与上一版本比较。

---

# 6. 阶段 C：Planning Harness

Planning 是 Harness 的核心能力之一。

---

## C1. Planner

输入复杂任务，输出结构化 Plan：

```json
{
  "goal": "...",
  "tasks": [
    {
      "id": "T1",
      "description": "...",
      "depends_on": [],
      "preferred_agent": "researcher",
      "priority": 1,
      "status": "pending"
    }
  ]
}
```

---

## C2. Task Model

Task 至少包含：

```text
id
description
depends_on
priority
preferred_agent
required_tools
status
attempts
result
error
```

状态：

```text
PENDING
READY
RUNNING
BLOCKED
COMPLETED
FAILED
SKIPPED
```

---

## C3. Task Graph

支持：

```text
Sequential
Parallel
Dependency
Optional Branch
Failure Branch
```

例如：

```text
       T1
      /  \
    T2    T3
      \  /
       T4
       ↓
       T5
```

---

## C4. Scheduler

根据当前 State 计算：

```text
ready_tasks
blocked_tasks
running_tasks
finished_tasks
```

Scheduler 尽量做确定性逻辑，不全部交给 LLM。

---

## C5. Plan-and-Execute

```text
User Task
   ↓
Planner
   ↓
Task Graph
   ↓
Scheduler
   ↓
Executor
   ↓
Observation
   ↓
继续 / Replan / Finish
```

---

## C6. Replanner

以下情况触发：

```text
Tool 连续失败
子任务失败
证据不足
发现新依赖
Reviewer REWORK
预算不足
任务目标发生变化
```

Replan 不能直接清空全部 Plan，而应该生成差异：

```text
add_tasks
remove_tasks
modify_tasks
reprioritize
```

---

## C7. Planning Eval

评测：

```text
Task Decomposition Quality
Dependency Accuracy
Plan Completion Rate
Unnecessary Task Rate
Replan Success Rate
```

### 阶段验收

输入一个至少需要 5 个步骤的任务：

1. 自动生成 Task Graph。
2. 正确并行无依赖任务。
3. 模拟一个 Tool 失败。
4. Replanner 能修改计划。
5. 最终仍可完成任务。

---

# 7. 阶段 D：Tool & Skill Harness

目标：从“有工具”升级为“有完整 Tool Runtime”。

---

## D1. Tool Schema

每个 Tool 定义：

```text
name
description
input_schema
output_schema
tags
risk_level
side_effect
requires_approval
timeout
retry_policy
```

---

## D2. Tool Registry

统一完成：

```text
register
unregister
search
filter
get
list
```

Agent 不直接 import 某个工具函数执行。

---

## D3. Tool Executor

统一执行链：

```text
Tool Call
   ↓
Schema Validation
   ↓
Permission Check
   ↓
Risk Check
   ↓
Approval?
   ↓
Timeout / Retry
   ↓
Execute
   ↓
Output Validation
   ↓
Result Processing
```

---

## D4. Tool Risk

示例：

```text
LOW
read / search / calculator

MEDIUM
write / create draft

HIGH
delete / send / external mutation
```

HIGH 默认需要 HITL。

---

## D5. Dynamic Tool Selection

不要把全部工具都暴露给每个 Agent。

根据：

```text
Role
Task
Skill
Permissions
Workflow Stage
Risk
```

筛选工具集。

---

## D6. Tool Result Processing

处理：

```text
超长结果截断
结果摘要
重复内容去重
结构校验
错误分类
```

---

## D7. Skill Registry

Skill 采用可扩展定义，例如 Markdown + Frontmatter：

```text
name
description
triggers
allowed_tools
instructions
version
```

---

## D8. Skill Routing

第一版：

```text
BM25 Recall
     ↓
LLM Rerank
     ↓
Skill Injection
```

并记录：

```text
候选 Skill
召回分数
最终选择
选择理由
```

---

## D9. Skill Eval

建立：

```text
skills/_tests/
```

每个 Skill：

```text
Positive Cases
Negative Cases
Confusion Cases
```

---

## D10. Subagent as Tool

实现：

```text
delegate_subagent(
  role,
  task,
  context_policy,
  budget
)
```

流程：

```text
Main Agent
    ↓
delegate_subagent
    ↓
Subgraph
    ↓
Subagent
    ↓
Structured Result
    ↓
Main Agent
```

### 阶段验收

Tool Runtime 能明确回答：

```text
为什么这个 Tool 对当前 Agent 可用？
是否需要审批？
失败后是否重试？
结果如何进入 Context？
这次 Tool Call 花了多少时间？
```

---

# 8. 阶段 E：Context Engineering

Context 与 Memory 分开建设。

---

## E1. Context Builder

每次模型调用统一经过：

```text
Context Builder
```

候选内容：

```text
System Instruction
Role Prompt
Skill Instruction
Current Task
Current Plan
Relevant Evidence
Relevant Memory
Recent Messages
Workspace Summary
Available Tools
Output Schema
```

---

## E2. Context Source Policy

每类内容定义：

```text
ALWAYS_INCLUDE
RETRIEVE_IF_RELEVANT
SUMMARY_ONLY
PRIVATE
NEVER_EXPOSE
```

---

## E3. Context Budget

建立 token budget，而不是无限拼接。

例如：

```text
Instructions       10%
Current Task       10%
Messages           20%
Evidence           30%
Memory             10%
Tool Context       10%
Reserve            10%
```

比例只作为策略，可动态调整。

---

## E4. Context Compression

支持：

```text
Message Trimming
Conversation Summary
Tool Result Compression
Artifact Summary
Evidence Deduplication
Old Observation Compression
```

---

## E5. Context Isolation

明确区分：

```text
Agent Private State
Shared Workspace
Handoff Context
User-visible Context
```

一个 Researcher 不需要看到 Writer 的全部私有历史。

---

## E6. Handoff Context Pack

统一结构：

```text
Task
Goal
Known Facts
Evidence
Progress
Artifacts
Open Questions
Constraints
Expected Output
```

Agent Handoff 不传全部 conversation。

---

## E7. Context Eval

对照：

```text
Full Context
vs
Context Engine
```

比较：

```text
Task Quality
Token Usage
Context Precision
Hallucination Rate
Latency
```

### 阶段验收

长任务中，Context 不会随着历史无限增长；不同 Agent 得到的 Context 能明显不同且可解释。

---

# 9. 阶段 F：Memory & Knowledge

---

## F1. Short-term Memory

使用：

```text
Graph State
Thread
Checkpointer
```

负责当前会话和当前任务。

---

## F2. Long-term Memory

跨 Thread Store 支持：

```text
remember
search
update
forget
```

---

## F3. Memory Types

### Semantic Memory

```text
事实
用户偏好
领域知识
```

### Episodic Memory

```text
历史任务
发生过的失败
成功执行经验
```

### Procedural Memory

```text
工作规则
操作方法
成功策略
```

---

## F4. Memory Write Policy

长期 Memory 不允许“全量自动记录”。

满足以下情况再写：

```text
用户明确要求
稳定偏好
已验证的重要事实
可复用经验
```

记录：

```text
source
confidence
created_at
updated_at
expires_at
```

---

## F5. Knowledge / RAG-lite

单独建立：

```text
knowledge/
```

第一版：

```text
BM25
```

后续可扩展：

```text
Embedding Retrieval
Hybrid Retrieval
Reranker
```

---

## F6. Memory Eval

评价：

```text
Recall Precision
Recall Rate
Wrong Memory Rate
Stale Memory Rate
Cross-thread Recall Rate
Irrelevant Injection Rate
```

### 阶段验收

新 Thread 能正确调用旧 Memory，但无关 Memory 不会全部进入 Prompt。

---

# 10. 阶段 G：Multi-Agent Orchestration

目标：在统一 Harness 上实现多种协作策略。

所有模式必须共享：

```text
AgentState
Runtime
Tool Registry
Context Engine
Memory
Workspace
Trace
Checkpoint
Eval
```

---

## G1. Role-based Agent

第一批角色：

```text
Researcher
Organizer
Writer
Reviewer
```

Role 只定义：

```text
Prompt
Skills
Tools
Model Profile
Permissions
Output Schema
```

Role 不创建自己的独立 Runtime。

---

## G2. Pipeline

```text
Research
   ↓
Organize
   ↓
Write
   ↓
Review
```

主要学习：

```text
Sequential Orchestration
State Passing
Stage Contract
```

---

## G3. Manager–Worker

```text
Manager
   ↓
Task Assignment
   ↓
Worker
   ↓
Result
   ↓
Manager
```

重点：

```text
Delegation
Worker Selection
Result Acceptance
Rework
```

---

## G4. Fan-out / Fan-in

Planner 动态生成多个任务：

```text
Planner
  ↓
Send(T1)
Send(T2)
Send(T3)
  ↓
Workers
  ↓
Reducer
  ↓
Synthesizer
```

重点学习：

```text
Dynamic Parallelism
Reducer
Concurrency
Partial Failure
Aggregation
```

---

## G5. Handoff

通过：

```text
active_agent
Command
Handoff Context Pack
```

实现：

```text
Agent A
   ↓
Handoff Decision
   ↓
Agent B
```

记录：

```text
from_agent
to_agent
reason
context_pack
```

---

## G6. Blackboard

所有 Agent 共享结构化 Workspace：

```text
facts
evidence
artifacts
decisions
questions
errors
progress
```

避免 Agent 之间反复复制大段消息。

---

## G7. Generator–Critic

```text
Generator
   ↓
Critic
   ↓
PASS / REWORK
```

必须限制：

```text
max_review_rounds
```

并记录 Critic 原因。

---

## G8. Debate-lite

只有满足以下条件才触发：

```text
证据冲突
高不确定性
关键决策
```

流程：

```text
Pro Agent
Con Agent
   ↓
Judge
```

不把 Debate 当默认模式。

---

## G9. Dynamic Team

Planner 动态决定：

```text
是否需要 Multi-Agent
需要哪些 Role
需要几个 Worker
哪些任务并行
是否需要 Reviewer
```

---

## G10. Orchestration Benchmark

同一任务集对比：

```text
Single Agent
Pipeline
Manager–Worker
Fan-out
Dynamic Team
```

比较：

```text
Task Success
Quality
Token
Latency
LLM Calls
Duplicate Work
Failure Rate
```

### 阶段验收

多 Agent 不再是多个孤立脚本，而是多个 Orchestration Policy。

---

# 11. 阶段 H：Reliability + Durable Execution + HITL

这是 Harness 工程非常重要的一层。

---

## H1. Checkpoint

所有正式 Run 使用：

```text
run_id
thread_id
checkpoint
```

---

## H2. Resume

模拟：

```text
Planner
 ↓
Worker 1 ✅
 ↓
Worker 2
 ↓
进程退出
```

重启后：

```text
Load Checkpoint
 ↓
继续 Worker 2
```

而不是从头执行。

---

## H3. Idempotency

有副作用 Tool 必须保护：

```text
write
send
create
delete
```

建立：

```text
idempotency_key
execution_record
```

防止 Resume 后重复副作用。

---

## H4. Retry Policy

明确区分：

```text
LLM Retry
Tool Retry
Node Retry
Task Retry
Plan Retry
```

错误分类：

```text
transient
validation
permission
rate_limit
timeout
permanent
```

---

## H5. Timeout

至少支持：

```text
Tool Timeout
Node Timeout
Task Timeout
Run Timeout
```

---

## H6. Loop Guard

检测：

```text
相同 Tool + 参数重复调用
Agent A ↔ Agent B 循环 Handoff
Reviewer 无限 REWORK
Planner 重复创建同一 Task
没有 State Progress 的循环
```

达到阈值：

```text
Retry
Replan
Interrupt
Stop
```

---

## H7. Guardrails

分三层：

```text
Input Guardrail
Tool Guardrail
Output Guardrail
```

例如：

```text
输入是否合法
当前用户是否能调用 Tool
工具参数是否合法
输出是否满足 Schema
```

---

## H8. Human-in-the-loop

实现三种 Interrupt：

```text
Approval
Edit
Clarification
```

例如：

```text
Agent
 ↓
delete_file
 ↓
HIGH RISK
 ↓
Interrupt
 ↓
Approve / Reject / Edit
 ↓
Resume
```

---

## H9. State Review

允许人工查看：

```text
Plan
Current Task
Active Agent
Pending Tool
Evidence
Artifacts
Errors
```

必要时编辑 State 再 Resume。

### 阶段验收

至少完成三个故障实验：

```text
Tool 临时失败 → Retry 成功
进程中断 → Resume 成功
高风险 Tool → HITL 后继续
```

---

# 12. 阶段 I：Model Routing + Budget Harness

---

## I1. Model Profiles

支持：

```text
fast
balanced
deep
cheap
```

Profile：

```text
provider
model
temperature
max_tokens
cost metadata
capability tags
```

---

## I2. Dynamic Model Routing

根据：

```text
Task Complexity
Agent Role
Context Length
Budget
Failure History
Required Capability
```

动态选择模型。

---

## I3. Budget Manager

控制：

```text
max_llm_calls
max_tool_calls
max_tokens
max_cost
max_iterations
max_parallelism
```

---

## I4. Graceful Degradation

预算不足时：

```text
减少 Worker
跳过 Debate
切换 Cheap Model
压缩 Context
取消低优先级 Task
降低 Review 次数
```

---

## I5. Usage Accounting

每次 Run 输出：

```text
usage.json
```

按：

```text
Run
Agent
Task
Model
Tool
```

统计：

```text
Input Tokens
Output Tokens
Cost
Latency
Calls
```

### 阶段验收

同一个任务可以以：

```text
low_budget
balanced
high_quality
```

三种策略运行并比较结果。

---

# 13. 阶段 J：MCP 与互操作

---

## J1. MCP Client

MCP Tool 进入统一 Tool Registry：

```text
MCP Server
   ↓
Tool Discovery
   ↓
Schema Import
   ↓
Tool Registry
   ↓
Harness
```

实现：

```text
Discovery
Schema Mapping
Permission Mapping
Invocation
Result Normalization
```

---

## J2. MCP Security

所有 MCP Tool 一样经过：

```text
Allowlist
Permission
Risk Level
Approval
Timeout
Retry
```

不能绕过 Harness。

---

## J3. 本地 MCP Server

至少把两个能力暴露出去：

```text
workspace_search
memory_search
```

同时学习 MCP Client / Server。

---

## J4. A2A 实验（P2）

等 Harness 主体稳定后再做：

```text
Agent Harness A
      ↓
     A2A
      ↓
Agent Harness B
```

目标是研究跨独立 Agent Runtime 的任务委派，而不是主线必需项。

### 阶段验收

当前 Harness 可以动态发现并调用至少一个 MCP Server 提供的 Tool。

---

# 14. 阶段 K：Web Agent Workbench

UI 在 Runtime 稳定后进入主线。

---

## K1. Run Dashboard

显示：

```text
Run Status
Current Task
Active Agent
Elapsed Time
Token
Cost
Errors
```

---

## K2. Graph Timeline

展示：

```text
Planner
Researcher #1
Researcher #2
Writer
Reviewer
```

每个 Node 状态：

```text
pending
running
completed
failed
waiting_human
```

---

## K3. Streaming

实时展示：

```text
Node Event
Agent Progress
Token Stream
Tool Call
State Update
Interrupt
```

---

## K4. Tool Card

展示：

```text
Tool Name
Arguments
Risk
Approval
Latency
Retry
Result
```

---

## K5. Plan View

展示 Task Graph：

```text
Pending
Ready
Running
Blocked
Completed
Failed
```

---

## K6. HITL Panel

支持：

```text
Approve
Reject
Edit
Clarify
Resume
```

---

## K7. Trace Viewer

查看：

```text
Graph
Trajectory
State Delta
Tool Events
Latency
Token
Cost
Errors
Artifacts
```

---

## K8. Workspace Viewer

查看：

```text
Evidence
Facts
Notes
Artifacts
Memory
Final Output
```

### 阶段验收

用户不打开终端，也能回答：

```text
Agent 正在做什么？
为什么做？
用了什么 Tool？
花了多少？
在哪里失败？
是否等待人工？
最终产生了什么 Artifact？
```

---

# 15. 最终 Eval 体系

## 15.1 Component Eval

```text
Skill Routing
Tool Selection
Tool Arguments
Memory Retrieval
Context Selection
Model Routing
Structured Output
```

---

## 15.2 Agent Eval

```text
Planning
Task Completion
Replanning
Tool Usage
Final Answer
```

---

## 15.3 Multi-Agent Eval

```text
Agent Assignment
Handoff Accuracy
Parallel Coverage
Duplicate Work
Synthesis Quality
Reviewer Value
Debate Trigger Accuracy
```

---

## 15.4 Harness Eval

```text
Recovery Rate
Loop Rate
Retry Success Rate
Checkpoint Resume Rate
HITL Trigger Accuracy
Tool Permission Accuracy
Cost
Latency
Token Usage
```

---

# 16. Ablation Experiments

最终至少完成：

```text
No Planning
vs
Planning
```

```text
No Memory
vs
Memory
```

```text
Full Context
vs
Context Engine
```

```text
Single Agent
vs
Multi-Agent
```

```text
Sequential
vs
Fan-out
```

```text
No Reviewer
vs
Reviewer
```

```text
Fixed Model
vs
Model Routing
```

```text
No Recovery
vs
Durable Execution
```

最终报告需要回答：

```text
质量提升多少？
成功率提升多少？
Token 增加多少？
延迟增加多少？
哪些任务适合 Multi-Agent？
哪些任务 Single Agent 更好？
哪些机制成本高但收益低？
```

---

# 17. 开发优先级

## P0：主线必须完成

```text
A0 从零初始化
A  Single-Agent Harness
B  Eval + Trace
C  Planning / Replanning
D  Tool & Skill Harness
E  Context Engineering
G  Multi-Agent Orchestration
H  Durable Execution + HITL
```

## P1：形成完整 Harness

```text
F  Memory / Knowledge
I  Model Routing / Budget
J1 MCP Client
K  Web Agent Workbench
```

## P2：展示与扩展

```text
J3 MCP Server
J4 A2A
Sandbox
Vector RAG
复杂长期记忆
生产部署
```

---

# 18. 从零开始的实际执行顺序

> 这一节就是实际开发时按顺序执行的 Checklist。

## Milestone 0：空目录 → 第一个 Agent

```text
01 创建项目目录和 Git
02 建 Python 虚拟环境
03 建 pyproject.toml
04 安装 LangGraph / Provider / pytest
05 建 .env.example + config
06 建 src / tests / eval / workspaces
07 写 Hello StateGraph
08 写 MockLLM Adapter
09 接真实 LLM
10 写 calculator Tool
11 写 current_time Tool
12 跑通最小 Tool Calling Loop
13 加最大循环终止
14 写第一批 Unit Test
15 创建 run_id / workspace
16 输出第一份 trace.jsonl
```

**里程碑结果：单 Agent MVP。**

---

## Milestone 1：单 Agent → Harness

```text
17 抽 Agent Runtime
18 定义 Lifecycle
19 定义 RuntimeContext
20 定义统一 AgentState
21 定义 Reducers
22 Structured Output
23 Tool Registry
24 Tool Executor
25 Tool Schema Validation
26 Tool Timeout
27 Tool Retry
28 本地 Trace Event Schema
29 Usage 统计
30 Streaming
```

**里程碑结果：Single-Agent Harness v1。**

---

## Milestone 2：先建立 Eval

```text
31 建 Benchmark Dataset
32 Tool Selection Eval
33 Tool Argument Eval
34 Trajectory Eval
35 End-to-End Eval
36 Benchmark Report
```

**里程碑结果：以后每次优化都可以量化。**

---

## Milestone 3：Planning

```text
37 Planner
38 Task Schema
39 Task Graph
40 Scheduler
41 Plan-and-Execute
42 Failure Observation
43 Replanner
44 Planning Eval
```

**里程碑结果：复杂任务可以拆解、执行和动态重规划。**

---

## Milestone 4：Tool / Skill Harness

```text
45 Tool Risk
46 Permission
47 Dynamic Tool Selection
48 Tool Result Compression
49 Skill Schema
50 Skill Registry
51 BM25 Skill Recall
52 LLM Rerank
53 Skill Eval
54 Subagent as Tool
```

**里程碑结果：工具和技能从“函数集合”升级成 Harness Runtime 能力。**

---

## Milestone 5：Context Engineering

```text
55 Context Builder
56 Context Source Policy
57 Context Budget
58 Message Trimming
59 Conversation Summary
60 Tool Result Compression
61 Artifact Summary
62 Context Isolation
63 Handoff Context Pack
64 Context Eval
```

**里程碑结果：每一次 LLM Call 都有可解释的 Context 构建过程。**

---

## Milestone 6：Memory

```text
65 Short-term Memory
66 Checkpointer
67 Long-term Store
68 Semantic Memory
69 Episodic Memory
70 Procedural Memory
71 Memory Write Policy
72 BM25 Knowledge Retrieval
73 Memory Eval
```

**里程碑结果：Agent 能跨 Thread 记忆，但不会无限污染 Context。**

---

## Milestone 7：Multi-Agent

```text
74 Role Registry
75 Pipeline
76 Manager–Worker
77 Fan-out / Fan-in
78 Blackboard
79 Handoff
80 Generator–Critic
81 Debate-lite
82 Dynamic Team
83 Orchestration Benchmark
```

**里程碑结果：多 Agent 只是统一 Harness 上的不同 Orchestration Policy。**

---

## Milestone 8：Reliability + HITL

```text
84 Checkpoint Resume
85 Idempotency
86 Error Classification
87 Retry Policy
88 Timeout Policy
89 Loop Guard
90 Input Guardrail
91 Tool Guardrail
92 Output Guardrail
93 Interrupt Approval
94 Interrupt Edit
95 Interrupt Clarification
96 State Review + Resume
```

**里程碑结果：Agent 长任务可以可靠运行、失败恢复并接受人工干预。**

---

## Milestone 9：Model + Budget

```text
97 Model Profiles
98 Complexity Router
99 Model Routing
100 Token Accounting
101 Cost Accounting
102 Budget Manager
103 Graceful Degradation
```

**里程碑结果：Agent 可以在质量、成本、速度之间动态取舍。**

---

## Milestone 10：MCP

```text
104 MCP Client
105 Tool Discovery
106 Schema Mapping
107 MCP Permission Mapping
108 MCP Tool Execution
109 MCP Security
110 本地 MCP Server
```

**里程碑结果：Harness 可以接入标准外部工具生态。**

---

## Milestone 11：Web Workbench

```text
111 Run Dashboard
112 Graph Timeline
113 Streaming UI
114 Tool Cards
115 Plan View
116 Workspace Viewer
117 Trace Viewer
118 HITL Panel
119 Eval Dashboard
```

**里程碑结果：形成可展示、可调试的 Agent Workbench。**

---

## Milestone 12：最终实验

```text
120 完整 Benchmark
121 Planning Ablation
122 Memory Ablation
123 Context Ablation
124 Single vs Multi-Agent
125 Sequential vs Fan-out
126 Reviewer Ablation
127 Model Routing Ablation
128 Durable Execution Failure Test
129 项目技术报告
130 README / 架构图 / Demo
```

**里程碑结果：项目不仅能运行，还能解释“为什么这样设计，以及这些机制是否真的有效”。**

---

# 19. 每一步统一完成标准

以后每个开发步骤都必须回答五个问题：

## 19.1 Code

功能是否已经实现？

## 19.2 Test

是否有自动化测试？

## 19.3 Trace

运行时是否能看到它发生？

## 19.4 Eval

是否有案例证明它有没有价值？

## 19.5 Docs

是否说明了：

```text
为什么需要它？
它解决什么问题？
什么时候应该用？
什么时候不应该用？
```

例如 Handoff 的验收不能只是：

```text
Agent A 可以切到 Agent B
```

还必须验证：

```text
为什么发生 Handoff？
是否选对 Agent？
传了哪些 Context？
是否泄露无关 Context？
Handoff 后任务成功了吗？
成本有什么变化？
```

---

# 20. 推荐三个最终 Demo

不要准备十个浅 Demo。

最终重点准备三个能把 Harness 能力串起来的 Demo。

---

## Demo 1：复杂研究任务

展示：

```text
Planner
Task Graph
Dynamic Team
Fan-out
Search Tool
Evidence
Blackboard
Context Engine
Synthesis
Reviewer
Final Output
```

体现：

```text
Planning
Tool Calling
Context
Multi-Agent
Evaluation
```

---

## Demo 2：失败恢复任务

主动让一个 Tool 第一次失败，并在中途模拟进程退出。

展示：

```text
Tool Error
Retry
Replan
Checkpoint
Resume
Loop Guard
Trace
```

体现：

```text
Reliability
Durable Execution
Harness Control Plane
```

---

## Demo 3：高风险操作任务

设计一个具有副作用的操作。

展示：

```text
Agent Decision
Tool Risk
Guardrail
Interrupt
Human Approval / Edit
Resume
Audit Trace
```

体现：

```text
HITL
Permission
Guardrail
State Resume
```

---

# 21. 技术决策 ADR

## D-001：核心 Runtime 使用 LangGraph

使用：

```text
langgraph
langchain-core
必要的模型 Provider Adapter
```

不重复实现底层 Graph Runtime。

---

## D-002：核心 Harness 使用 Graph API

主流程显式使用：

```text
State
Node
Edge
Command
Send
Subgraph
```

高层 Agent Helper 可以用于 Leaf Agent，但不能隐藏 Harness 核心逻辑。

---

## D-003：Mock First

以下能力必须尽量能够 Mock：

```text
Tool Routing
Planning
Handoff
Retry
Interrupt
Recovery
```

这样 Unit Test 不依赖真实模型和 Token。

---

## D-004：LangGraph Runtime 与 Harness Policy 分离

LangGraph 负责：

```text
Graph 怎么运行
```

Harness 负责：

```text
运行什么
给谁运行
看到什么 Context
开放什么 Tool
允许执行什么
失败怎么办
什么时候需要人
什么时候停止
怎么评估
```

---

## D-005：RAG / Memory / Context 分离

```text
RAG
= 从知识源寻找信息

Memory
= 跨时间保存和召回信息

Context
= 当前一次模型调用具体看到什么
```

---

## D-006：本地 Trace 优先

LangSmith 可以接入，但不是项目运行的必要条件。

不开 LangSmith 时，仍必须有：

```text
trace.jsonl
run.json
usage.json
workspace
benchmark report
```

---

## D-007：Multi-Agent 是 Policy，不是多个 Runtime

```text
Pipeline
Manager–Worker
Fan-out
Handoff
Debate
Dynamic Team
```

都运行在统一 Harness 上。

---

# 22. 最终知识覆盖图

项目完成后应能明确展示：

```text
LangGraph StateGraph
State / Reducer
Node / Edge
Command / Send
Subgraph

Agent Loop
ReAct
Structured Output

Function Calling
Tool Calling
Tool Registry
Tool Schema
Tool Executor
Dynamic Tool Selection
Tool Retry
Tool Timeout
Tool Permission
Tool Risk

Skills
Skill Registry
Skill Routing
Skill Eval

Planning
Task Decomposition
Task Graph
Scheduler
Plan-and-Execute
Replanning

Context Engineering
Context Selection
Context Budget
Context Compression
Context Isolation
Handoff Context

Short-term Memory
Long-term Memory
Semantic Memory
Episodic Memory
Procedural Memory
RAG

Workspace
Artifacts
Blackboard

Subagent
Role-based Agent
Pipeline
Manager–Worker
Fan-out / Fan-in
Handoff
Generator–Critic
Debate
Dynamic Team

Checkpoint
Persistence
Durable Execution
Idempotency
Retry
Timeout
Loop Detection
Failure Recovery

Human-in-the-loop
Interrupt
Resume
Guardrails
Approval

Model Routing
Budget Control
Token / Cost Accounting
Graceful Degradation

Tracing
Trajectory Evaluation
Agent Benchmark
Ablation

MCP Client
MCP Server
Streaming
Multi-Agent UI
```

---

# 23. 项目最终成功标准

项目是否成功，不看：

> “一共实现了多少个 Agent。”

而看下面五件事：

### 1. Runtime

复杂 Agent Workflow 能不能稳定执行？

### 2. Harness

Context、Tool、Memory、Budget、Guardrail、Retry 等是否统一管理？

### 3. Multi-Agent

不同协作模式是否建立在同一 Harness 上？

### 4. Reliability

是否支持 Checkpoint、Resume、HITL、失败恢复？

### 5. Evaluation

是否能通过 Benchmark 和 Ablation 证明这些机制在什么任务上有效？

最终项目应该能够被描述为：

> **从零基于 LangGraph 构建了一套完整 Agent Harness，覆盖 Planning、Tool Runtime、Context Engineering、Memory、多智能体编排、Durable Execution、HITL、Guardrails、Model Routing、Evaluation 与 MCP，并通过统一 Benchmark 对不同 Agent 策略进行可量化比较。**
