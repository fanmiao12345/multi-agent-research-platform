# agent-mvp —— 从零基于 LangGraph 的 Agent Harness

> 主线规划与逐步骤执行状态见 **`DEV_PLAN_LangGraph_Harness_From_Scratch.md`**（文档编号 01~130）。
> 旧版零依赖实现（单/多智能体 CLI 等）保留在 `legacy/` 供参考与教学。

## 这是什么

一套 **轻量 Agent Harness + Multi-Agent Runtime**：LangGraph 负责 Graph 如何运行
（State/Node/Edge/Checkpointer/条件路由），本项目负责 Agent 如何运行——看到什么
Context、开放什么 Tool、允许做什么、失败怎么办、何时要人、花多少钱、怎么评估。

```
Application / CLI / Web Workbench
        ↓
Multi-Agent Orchestration（orchestration/*）
        ↓
Agent Harness（harness/*：runtime / context / tools / memory / planning / control…）
        ↓
LangGraph Runtime（langgraph 提供图执行、状态、checkpoint）
        ↓
LLM Adapter / Tools / MCP / 外部服务
```

## 快速开始

```bash
# 1) 依赖（虚拟环境就绪时跳过）
python -m venv .venv
.venv\Scripts\python -m pip install langgraph langchain-core openai pytest python-dotenv

# 2) 配置（真实模式必需；离线演示需明确选择 Mock）
copy .env.example .env          # 填 MODEL_API_KEY / MODEL_PROVIDER 等

# 3) 测试（全离线）
.venv\Scripts\python -m pytest

# 4) 统一任务入口（默认Mock，产生根账本及run/trace/usage）
.venv\Scripts\python -m src.interfaces.cli "计算6*7"

# 5) 评测（全部离线、可复现）
.venv\Scripts\python -m eval.benchmark              # Agent Eval
.venv\Scripts\python -m eval.benchmark_orchestration
.venv\Scripts\python -m eval.benchmark_model
.venv\Scripts\python -m eval.final_report           # 汇总 + TECH_REPORT

# 6) Web Agent Workbench（9 面板：Dashboard/Timeline/ToolCards/Plan/…）
.venv\Scripts\python -m src.interfaces.web.workbench --port 8765
#   打开 http://127.0.0.1:8765/
```

Web默认选择 **Mock离线演示**，可切换到真实模型。配置区显示实际模型并阻止无效配置；
静态检查通过不代表已经验证网络、认证或工具能力。修改`.env`后需要重启服务。
当前内置工具仅有计算和时间；B3/B4 资料导入、B5 研究写作链、S4 状态库/阶段恢复、
**S5 研究任务工作台**（队列/进度/停止/恢复/证据引用对照/版本/导出/写接口安全）均已接入；
agent 任务仍直跑（页面入口随 S5 已提供）。

B2已接入统一任务入口与根账本。Web可设置调用次数、输出Token、运行秒数和费用阈值，
最终回答下方显示整项任务用量。CLI默认上限为12次调用、8192输出Token、300秒；
真实模式未指定费用阈值时默认0.05美元参考估算。`--max-calls 0`可验证零调用停止。
时间在调用边界检查，不能强杀后台工具；费用不是账单硬封顶。模型SDK隐藏重试已关闭。
详细用法、数据位置与限制见`docs/B2_DELIVERY.md`。

```powershell
# B3/B4：带资料运行（粘贴文本 / TXT/Markdown 文件只读 / 用户指定网页链接）
.venv\Scripts\python -m src.interfaces.cli "整理资料" --import-file D:\资料\笔记.md --import-text "补充粘贴。" --import-url https://example.com/article
# 资料登记在 workspaces\jobs\job_<id>\sources.json；全文在 sources\，段落定位在 <id>.meta.json
# Web 运行带资料的任务后，⑳面板可查看来源解析状态、去重结果与完整产物
```
资料分类：ok/partial/duplicate/empty/unsupported/too_large/read_failed 全部可查；
单任务≤20来源、单来源≤2MB、单页下载≤10MB，超限明确拒绝不静默截断；同文转载自动去重。
网页抓取默认安全策略：仅 http(s)、拒绝私网/回环/链路本地地址、重定向逐跳复检；
未配置搜索服务时搜索明确禁用（不会假装搜过）。
完整说明见`docs/B3_DELIVERY.md`与`docs/B4_DELIVERY.md`。

```powershell
# B5：研究写作链（带可核查引用的报告；默认Mock下请在真实模式前使用真实模型或桩评测）
.venv\Scripts\python -m src.interfaces.cli "整理材料并写一份带引用的报告" --flow research `
  --import-file D:\资料\笔记.md --import-url https://example.com/article
# 产物：workspaces\jobs\job_<id>\evidence.json、artifacts\material_pack.v1.md、
#       outline.v1.md、report.v1.md（修订 report.v2…不覆盖）、review.v1.json、pipeline.json
# 结果分级：accepted=验收通过；draft=待完善草稿（修订耗尽/预算/缺口，退出码1）；failed=阶段失败
```
链的每条断言都带 [E-编号]，可在 evidence.json 找到来源与原文定位；审校先由程序检查
引用/章节/覆盖，再由模型检查支持关系；最多修订 2 轮并保留全部版本。
说明与限制见`docs/B5_DELIVERY.md`。

```powershell
# S4：任务在阶段边界崩溃后可续跑（跳过已完成阶段；真实模型模式验收）
.venv\Scripts\python -m src.interfaces.cli --workspace workspaces --resume-job job_<id>
# 状态库：workspaces/state.sqlite（任务/会话/审批/操作账本）
# 阶段检查点：workspaces/jobs/<job_id>/stage_*.json —— 产物先落盘、状态后提交
```
S4 说明与限制见`docs/S4_DELIVERY.md`。

```powershell
# S5：页面研究任务（Mock 离线演示不能产出链式结构化内容，会明确失败；真实模式需 .env）
.venv\Scripts\python -m src.interfaces.web.workbench --port 8765
# → 表单流程选“研究写作链”→ ㉑研究任务卡跟踪进度/停止/恢复，正文 [E-编号] 引用点开证据原文定位
```
S5 说明与限制见`docs/S5_DELIVERY.md`。

```powershell
# S6：业务评测（真实模式需 .env；缺Key整批 not_executed，不拿 Mock 顶替）
.venv\Scripts\python -m eval.business_eval --mode real --max-cost 0.1
# 运维：健康检查 / 备份 / 全新环境验证 / 锁依赖
.venv\Scripts\python -m src.ops.health
.venv\Scripts\python -m src.ops.backup --workspace workspaces --out .tmp\backup
.venv\Scripts\python -m src.ops.verify
.venv\Scripts\python -m pip install -r requirements.lock.txt
```
S6 说明与限制见`docs/S6_DELIVERY.md`。

```powershell
# 只检查配置，不发送模型请求
.venv\Scripts\python -m src.harness.models.factory --mode real
# 检查20个业务案例和10个故障案例的定义，不等于执行业务验收
.venv\Scripts\python -m eval.research_cases
```

真实冒烟评测需显式指定`--mode real --max-cost`，例如
`.venv\Scripts\python -m eval.benchmark --mode real --task b01 --max-cost 0.02`。
该命令会请求配置的模型服务；费用阈值按返回用量估算，最后一笔可能超过阈值。
真实报告和Mock报告分别保存；`--no-skip`单独使用会报错，不会用Mock冒充真实评测。

## 目录速览

| 路径 | 内容 |
|---|---|
| `src/graph/` | LangGraph 状态模型与 Agent Loop 图 |
| `src/harness/` | Runtime、Context、Tools、Skills、Memory、Planning、Control、MCP、可观测 |
| `src/harness/storage/` | 资料与产物存储：路径边界、来源登记/去重/定位、受控 Artifact（S2/B3） |
| `src/harness/state/` | S4 状态库：SQLite任务/会话/审批/操作账本、队列租约、阶段恢复（S4） |
| `src/harness/ingest/` | 网页抓取与安全边界：URL策略/SSRF防护、正文提取、搜索网关占位（S2/B4） |
| `src/orchestration/` | Pipeline / Manager-Worker / Fan-out / Debate / Dynamic Team 策略 |
| `src/agents/` | Role Profile（数据化角色） |
| `src/mcp/` | 零依赖 MCP Client/Server + 本地 server |
| `src/interfaces/web/` | Workbench 服务端与前端 |
| `src/application/`、`src/interfaces/cli.py` | Web/CLI/评测共用请求与应用入口 |
| `workspaces/jobs/` | 根任务 request.json、ledger.json、job.json 与 sources/、artifacts/ |
| `eval/` | 数据集、评测器、benchmark 与最终报告 |
| `knowledge/` `skills/` | 知识库与技能（数据驱动、热插拔） |
| `workspaces/` | 每次运行的 run.json/trace.jsonl/usage.json/plan.json |
| `docs/` | 技术报告与架构文档 |
| `legacy/` | 旧零依赖实现（保留） |

## Demo 配方（组件演示，不代表完整业务流程已接通）

- 复杂研究写作：Planner → TaskGraph → Fan-out → Reviewer（`orchestration/manager_worker`）
- 失败恢复：durable resume 实验 → Web 面板看 trace
- 高风险操作：注册需要审批的工具后，HITL 面板会展示具体工具与参数；批准后执行，拒绝后返回拒绝结果。默认计算器和时间工具无需审批。

## 运行保护与恢复（2026-09-07 修复）

- **运行结果**：`run.json` 保存 `final_text`、`termination_reason`、`error` 和结束时间。
  模型或回调抛异常时记录失败终态、已知用量及 `run_end`；异常详情不写入产物，只记录异常类型。
  Web 查看任务可看到最终回答和失败状态；刷新页面后再次点击「查看」仍能读取回答。
- **预算**：通过 `RuntimeContext.from_settings(max_cost=0.01)` 或 POST `/api/runs`
  的 `max_cost` 字段设置单次 run 的累计估算美元成本阈值。`0` 禁止调用模型，`None` 不限。
  每次模型调用前后检查；达到阈值后不再执行后续工具或模型调用，原因是 `budget_exceeded`。
  **这不是供应商账单的硬封顶**：用量在响应返回后才可知，最后一笔请求可能超过阈值；
  价格使用本地估算表。有限预算下遇到未知定价或缺失用量会停止，不能默认为免费。
  多智能体各 run 的总预算仍需编排层管理。
- **工具超时**：超时后及时返回，不自动重试；同一 Executor 内，上次调用未结束时不再启动同名工具。
  Python 后台线程可能继续执行，超时不能撤销副作用；带 `side_effect=True` 的工具遇到瞬时错误也不自动重试。
  需要强制终止的工具应在独立进程中实现，目前没有通用进程沙箱。
- **崩溃恢复**：`resume_plan` 保留已完成结果；遗留 `RUNNING` 时会明确报错，
  确认原进程已退出、任务幂等或已核实副作用后，用 `resume_plan(run_dir, worker, retry_interrupted=True)` 重跑剩余任务。
  计划采用原子写入。恢复要求单个调用方持有计划，不提供跨进程锁或外部操作的 exactly-once 保证。
- **审批**：`HIGH` 或 `requires_approval=True` 任一条件成立都要审批。
  `AgentRuntime(..., tool_executor=executor)` 可接自定义工具，`run_task(..., approval_handler=handler)`
  中的 `handler(spec, arguments)` 只有返回 `True` 才放行。
  Web 服务可通过 `make_server(tool_registry=registry)` 注入本地受信任的工具注册表。
  审批绑定到 `run_id + request_id`，过期、重复、无待处理调用的决策返回 409，非法 action 返回 400。
  Web 默认等待 300 秒，超时视为不批准；内存中的待审批请求不跨服务重启恢复。

回归测试：`tests/test_runtime_regressions.py` 和 `tests/test_workbench.py` 覆盖真实子进程退出、
失败收尾、预算、超时、HTTP 审批和并发任务关联。Web 所有动态内容通过文本 DOM 渲染，避免作为 HTML 执行。

## 文档地图

- `docs/PRACTICAL_RESEARCH_WRITING_PLAN.md`：**实用化优化计划**——产品边界、分阶段任务、发布门槛（B1~B5、S4核心与链恢复、S5核心工作台、S6评测运行器与运维已完成离线验收；真实执行项与 S7 待实施/按证据）。
- `docs/IMPLEMENTATION_TRACKER.md`：**完整实施清单**——72项计划的当前状态、阶段顺序和未完成点。
- `docs/IMPLEMENTATION_LOG.md`：**逐步实施日志**——每完成一步追加改动、验证证据、限制与下一步。
- `docs/B2_DELIVERY.md`：**B2交付说明**——统一入口、根账本、限制、用法与兼容性。
- `docs/B3_DELIVERY.md`：**B3交付说明**——本地资料导入、来源登记/去重/定位、受控产物存储、路径边界。
- `docs/B4_DELIVERY.md`：**B4交付说明**——URL抓取与正文提取、网络安全边界、搜索占位网关。
- `docs/B5_DELIVERY.md`：**B5交付说明**——研究写作链（证据→素材→提纲→初稿→双层审校→有限修订）、引用可定位与草稿分级。
- `docs/S4_DELIVERY.md`：**S4交付说明**——SQLite状态库/队列租约/审批/操作账本、两段取消与阶段恢复。
- `docs/S5_DELIVERY.md`：**S5交付说明**——研究任务 Web 工作台（队列/进度/停止/恢复/引用对照/版本/导出/写接口安全）。
- `docs/S6_DELIVERY.md`：**S6交付说明**——真实评测运行器/人工评分表/运维工具/试用模板（离线验收；真实执行待 Key）。
- `docs/TRIAL_LOG_TEMPLATE.md`：**7 天个人试用日志模板**（S6-10，待真实执行）。
- `docs/RESEARCH_WRITING_ACCEPTANCE.md`：**业务验收基线**——20个业务案例、10个故障案例、评分和模式边界；业务尚未执行。
- `DEV_PLAN_LangGraph_Harness_From_Scratch.md`：**总计划**（项目定位、13 个里程碑、编号步骤 01~130、ADR、Ablation 设计；顶部挂执行状态指针）
- `docs/EXECUTION_STATUS.md`：**执行状态总账**——里程碑↔代码↔测试对照、离线评测基线、已知缺口与待办（最近更新 2026-09-09）
- `docs/architecture.md`：分层架构 + 一次运行的数据流 + 设计决策
- `docs/TECH_REPORT.md`：技术报告（里程碑验收映射 + 质量证据，测试计数每次生成时自动实测刷新）
- `eval/reports/`：Benchmark 与最终实验报告（json + md）
- `legacy/README.md`：旧零依赖实现文档（独立保留）
