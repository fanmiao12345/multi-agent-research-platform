# 执行状态总账（当前主线）

最近更新：2026-09-11。本次完成 D6-05：dynamic_team 接入动态计划/重规划、并发子任务和预算结算；验证通过。

## 唯一当前计划

[PROJECT_MASTER_PLAN.md](PROJECT_MASTER_PLAN.md) 定义完整个人 AI 智能体系统：D0～D10 共 48 个功能步骤 → 功能冻结 → Q1～Q4 共 12 个整体测试与优化步骤。先实现所有必做能力，再集中整体测试优化；开发期间只做最小功能验证和阻塞修复。

[IMPLEMENTATION_TRACKER.md](IMPLEMENTATION_TRACKER.md) 记录逐项状态，[IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md) 保存证据，[OPTIMIZATION_BACKLOG.md](OPTIMIZATION_BACKLOG.md) 收集非阻塞优化。

## 当前进展和下一步

- D0～D4：文档、统一请求/预算、真实资料来源、Context/Skill/Memory/Knowledge 已功能完成。
- D5-01：任务理解和关键条件识别已接通；缺对象/动作写 `input_request.json` 并进入 `waiting_input`，缺资料单独记录不反复追问。
- D5-02：六模式能力目录已定义；D6-01～03 后开放 single/fixed/manager_worker/fanout，选型按工具、模型、网络和预算过滤。
- D5-03：任务 ID、角色、依赖、循环、章节覆盖、预算和并发完成程序校验；fanout 按拓扑依赖派工。
- D5-04：计划带版本和失效关系；重规划保留无关已完成结果，下游旧成果失效重排；重复计划标记无进展。
- D6-01～03：single、fixed、manager_worker 已接入统一根任务；manager_worker 按依赖顺序交接上游产出，writer 仍由共享研究链完成。
- D6-04：fanout 已按依赖波次真实并发；并发数受 max_parallel 限制，每任务先预留后结算，成功与失败分支均落结构化记录。
- D6-05：dynamic_team 已接入 plan_task/replan、有限重规划和并发子任务；角色成果结构化回写。
- 下一步：**D6-06 debate** → D6-07 嵌套派工 → D6-08 六模式统一注册。
- Q1～Q4：尚未开始，必须等 D 步骤全部完成并冻结。

## 2026-09-11 代码核对基线

| 领域 | 已有基础 | 当前不能声称完成的部分 |
|---|---|---|
| 研究流程 | 来源/PDF 导入、证据、素材、提纲、报告、审校、改稿 | 完整多方式交付与整体业务验收未完成 |
| 入口 | CLI 与 Web 可执行任务；CLI 有初版 auto | Web 仍固定；完整多模式参数与自动选型尚未统一 |
| 搜索 | bing_scrape、候选元数据、正文来源、PDF 与共享来源库 | 真实抓取受反爬/结构变化影响；site/since 尚未暴露到 CLI/Web |
| 协作 | single/fixed/manager_worker/fanout 已接入，fanout 真并发 | dynamic_team/debate 与嵌套派工在 D6 后半接入 |
| 账本与证据 | 根账本、证据定位、根共享来源版本/撤回/下游引用 | 子报告到原始证据的完整映射在 D7 继续 |
| 运行与 UI | SQLite、队列、阶段恢复、版本和部分工作台 | 根/子取消恢复、持久审批和自动模式展示需要贯通 |

本次 D3-04/05 + D4 + D5 联合批次验证：259 passed（PDF、共享来源、Context、Skill、Memory、Runtime、搜索、导入、研究链、规划/重规划与编排）。本次 D6-01～03 子批次：82 passed；D6-04 并发与编排回归：38 passed；D6-05 动态团队/编排回归：53 passed。以上均不是全量结果；未重跑历史 433/434/437 批次。

## 历史状态与交付证据（不作为当前排期）

以下保留原总账，包括旧日期、测试数和“下一步”。这些表述描述当时状态，当前优先级只由本文件上半部及总计划决定。若历史与现有代码不符，以新记录明确纠正，不重写历史成绩。

### 历史执行状态总账

> 本文件是 `DEV_PLAN_LangGraph_Harness_From_Scratch.md` 的落地对照表：
> 每个里程碑/步骤组 → 交付物（代码/测试）→ 证据 → 已知缺口。
> 更新规则：每完成一步优化后刷新测试计数与"最近更新"。

- 最近更新：2026-09-10（批量开发：S8-00 纠偏落地 + 模拟搜索 mock provider + S8-01~04 首版实现；**数据集扩至 v2：33 业务案例（v1 冻结 20 + v2 机制扩充 13）+ 10 故障**；全量 **433 passed / 0 failed**。真实批次与页面级验收未做，详见 IMPLEMENTATION_LOG 同日条目）
- 最近修复验证：**414 passed**（链内硬约束闸门 + 章节取法误报修复；已知 `test_workbench_s5::test_write_api_security_guards` 在全量高负载下偶发连接中断，单文件重跑 7/7 通过，记录不修复）。此前基线：链内闸门 412、评测 Agent+人工确认 403、S5 378、S4 372、B5 347、B4 330、B3 255、B2 199、B1 174。
- 验证命令：`.venv\Scripts\python -m pytest`（期望全绿）
- 评测命令：`python -m eval.benchmark` / `eval.benchmark_orchestration` /
  `eval.benchmark_model` / `eval.final_report` / `eval.business_eval --mode real --max-cost X`
- 运维：`python -m src.ops.{health,backup,verify}`
- Web：`python -m src.interfaces.web.workbench --port 8765`

## 里程碑对照表

下面的 ✅ 表示对应组件已实现并有测试，**不表示所有组件已接入主 Runtime 或达到生产验收**。
当前主流程的接入范围及限制以本文件的「运行可靠性修复」和 README 的「运行保护与恢复」为准。

| 计划范围 | 里程碑 | 交付落点（代码） | 测试证据 | 状态 |
|---|---|---|---|---|
| A0（步骤 01-16） | 从零到第一个 Agent | `src/graph/hello_graph.py`、`src/graph/agent_loop.py`、`src/llm/{base,mock,provider}.py`、`src/builtin_tools/`、`config/settings.py`、`src/harness/{run_store,tracer}.py` | test_graph / test_llm_mock / test_tools / test_agent_loop | ✅ |
| A1（17-30） | Single-Agent Harness | `harness/runtime/*`、`graph/{state,reducers}.py`、`harness/tools/*`、`harness/structured.py`、`harness/usage.py`、Streaming | test_harness_runtime / test_tool_harness / test_stream_usage | ✅ |
| A2（31-36） | Eval 先行 | `eval/{datasets,benchmark.py,evaluators/*}` | test_eval | ✅ |
| M3（37-44） | Planning | `harness/planning/*` | test_planning | ✅ |
| M4（45-54） | Tool/Skill/Subagent | `control/policy.py`、`tools/result_processor.py`、`skills/`、`harness/skills/*`、`tools/subagent.py` | test_milestone4 | ✅ |
| M5（55-64） | Context Engineering | `harness/context/*`、`eval/evaluators/context_metrics.py` | test_context | ✅ |
| M6（65-73） | Memory & Knowledge | `harness/memory/*`、`harness/durable.py` 基础、`knowledge/` | test_memory | ✅ |
| M7（74-83） | Multi-Agent Orchestration | `src/agents/profiles.py`、`src/orchestration/*` | test_orchestration | ✅ |
| M8（84-96） | Reliability + HITL | `control/{errors,policies,loop_guard,guardrails,hitl,idempotency}.py`、`harness/durable.py`、`state_review.py` | test_reliability | ✅ |
| M9（97-103） | Model + Budget | `harness/models/*`、`budget_control.py`、`accounting.py` | test_model_budget | ✅ |
| M10（104-110） | MCP | `src/mcp/*`（零依赖 JSON-RPC） | test_mcp（含真实子进程集成） | ✅ |
| M11（111-119） | Web Workbench | `src/interfaces/web/workbench.py`（9 面板） | test_workbench | ✅ |
| M12（120-130） | 最终实验与文档 | `eval/final_report.py`、`README.md`、`docs/*` | test_final | ✅ |

## 离线评测基线（Mock 大脑，可复现）

| 评测 | 结果要点 |
|---|---|
| Agent Benchmark（13 任务/12 类） | executed 5/5通过，8项明确跳过；原因区分能力缺失与范围，Mock/真实报告分离 |
| Skill Eval | overall / positive / negative accuracy = 100% |
| Orchestration 对比 | single=1 次调用 → pipeline=4、manager/fanout/dynamic≈3（更长产出） |
| Model 三档 | low_budget→cheap、工具任务永不落 no_tools 的 deep |
| 三项故障实验 | Tool 失败→Retry 成功；进程中断→Resume 只跑剩余；高风险→HITL 放行/拦截 |

（最新数字以 `eval/reports/metrics_all.json` 与 `final_report.md` 为准。）

## 关键设计红线（遵守情况）

- D-001 LangGraph 承担图运行时 ✅（未自研 checkpoint）
- D-003 Mock First ✅（默认评测离线；真实模式需显式启用）
- D-004 Runtime 与 Policy 分离 ✅
- D-005 RAG/Memory/Context 分离 ✅
- D-006 本地 Trace 优先 ✅（run.json/trace.jsonl/usage.json 齐备）
- D-007 Multi-Agent 是 Policy ✅（统一 Harness 上的六策略）

## 已知缺口与待办（诚实清单）

1. **真实模型侧评测/成本/延迟**：需在 `.env` 配置 Key 后按 README Demo 章节补跑；
   读数接口（usage.json、摘要行、benchmark 框架）已全部就绪。
2. **计划文档编号 01~130**：以"等效代码 + 自动化测试 + 逐轮汇报"落地，
   文档本身未逐行打勾；如需精确到步骤号的勾选表可基于本文件扩展。
3. **B7 LangSmith Adapter**：仅保留可选接入点，未实现（本地 Trace 完整可用）。
4. **J4 A2A 实验 / P2 项**（Sandbox、Vector RAG、复杂长期记忆、生产部署、
   官方 MCP SDK 切换）：设计为后续扩展，未实施。
5. **Web Workbench**：纯标准库 MVP；富前端/与 legacy 前端合并未做。
   Plan/Workspace 等 API 不等于已具备完整可视化面板。
6. **组件接入范围**：Context、Memory、完整 LoopGuard、自动模型降级等仍有独立组件/演示，
   不应仅依据单元测试认为它们已在每次 AgentRuntime 调用中生效；本次只接入单 run 成本阈值和工具审批。

## 运行可靠性修复（2026-09-07）

| 问题 | 修复与验证 |
|---|---|
| 异常后永久 running | Runtime 统一收尾：失败终态、已知用量、run_end；run/stream 与事件回调异常回归 |
| 工具超时仍等待 | daemon + Future 有界等待；禁止超时自动重试和同一 Executor 的同名重叠调用；子进程退出测试 |
| 真正中断的 RUNNING 无法恢复 | 实测子进程 os._exit 后遗留状态；明确提示风险，retry_interrupted=True 后仅重跑剩余任务；计划原子写入 |
| max_cost 无效 | 接入模型调用前后检查，零预算零调用，超过阈值停止，未知价格/缺失用量停止 |
| 审批漏拦截/只写文件 | HIGH 或显式审批即拦截；HTTP 待审批→批准/拒绝→继续；过期、重复、错误 request_id 被拒绝 |
| HTML 注入和页面编码 | 动态内容通过 textContent/DOM 构建；修正 HTML 被 JSON 编码的问题；浏览器确认无图片元素和事件执行 |
| 最终回答丢失 | run.json 与 final 事件持久化；HTTP 和浏览器均可读取普通问答最终回答 |
| Web 启动关联错误 | 使用本次 Runtime 的 run_start 回调取得 id；并发 HTTP 请求分别关联各自任务 |

限制：预算基于响应返回后的用量与本地价格估算，最后一笔请求可能越过阈值，
不等于账单硬封顶；后台线程不能强杀；恢复不保证外部副作用 exactly-once；
Web 待审批请求只在当前服务进程中有效。真实模型费用和质量未在本轮测试。

## B1实用化第一批（2026-09-07）

- 交付：20个业务案例（8整理、8研究、4改稿）及10个故障案例；来源正文、引文、计算预期、改稿原稿、评分标准齐备。
  `eval.research_cases`检查定义有效，但业务执行次数仍为0，所有案例保留not_run。
- 模型：真实模式缺配置明确失败，不回退Mock；未指定档案时保留项目模型配置。
  配置repr隐藏Key/接口地址，模型调用错误不暴露供应商原始正文或异常链。
- Web：Mock/真实显式选择；静态配置诊断；run记录实际mode/provider/model；真实与Mock评测面板分别取报告。
- 评测：`--mode real`必须给`--max-cost`，`--no-skip`不能隐式切为真实模式；
  缺工具、Mock专用断言、预算停止分别记录；异常保留失败行，未知成本阻止后续真实任务。
- 验证：174项离线测试；5项Mock冒烟通过、8项跳过；真实适配器用替身响应验证普通回答和计算工具往返；
  CLI配置失败和错误评测参数退出码均为2；真实浏览器验证通过。没有实际模型付费请求。
- 版本基线：仓库仍无首个Git提交，不批量提交未跟踪文件。本批文件使用显式清单保存到项目`.tmp/`快照，
  校验清单见`docs/B1_BASELINE.json`；这只是本批文件快照，不是整个项目或用户数据的备份。

详细验收规则见`docs/RESEARCH_WRITING_ACCEPTANCE.md`。B1完成的是入口与验收基础；
真实服务认证、模型质量、完整业务执行尚未验收；真实评测费用仍是本地估算停止阈值。

## B2统一入口与根账本（2026-09-09）

- Web、CLI、Agent Benchmark统一调用ResearchApplication；根任务保存请求、账本和终态，旧run布局兼容。
- 主循环与Planner/Replanner/Judge/Rerank/摘要都接调用网关，线程继承同根预算，角色/父子run可追踪。
- 次数、输出Token、时间与费用初始限制；零限额零调用，未知用量不当免费；SDK隐藏重试关闭。
- 验证199项回归；5项Mock冒烟通过、8项跳过；浏览器确认调用数为0的停止和计算42后的2次调用账本。
- 完整说明见B2_DELIVERY.md，逐项进度见IMPLEMENTATION_TRACKER.md，每步证据见IMPLEMENTATION_LOG.md。
- 没有真实付费请求。恢复、排队取消、细分模型错误/重试与实际业务成稿仍未完成。

## B3本地资料导入与受控产物存储（2026-09-09）

- 路径边界：新增`src/harness/storage/{paths,sources,artifacts}.py`；Windows junction实测发现
  realpath对“不存在尾部”不解析中间链接，改为最长已存在前缀解析后判包含性；../、绝对路径越界、链接逃逸全拒。
- 来源：粘贴文本/TXT/Markdown导入，ok/partial/duplicate/empty/unsupported/too_large/read_failed全分类登记；
  sha256+规范化哈希判重（转载只留duplicate_of），标题+段落+字符偏移定位存meta；UTF-8→GB18030→替换降级；单来源2MB/任务20来源/累计10MB明确拒绝不静默截断。
- 产物：artifact_id=kind.vN，版本自动递增永不覆盖，读取校验内容哈希；索引损坏/重复id/孤儿文件拒绝访问；B5将接模型读取工具。
- 入口：TaskRequest.texts/files，ResearchApplication.run先导入后运行（零可用来源明确失败），job.json记import摘要；CLI
  `--import-file/--import-text`带sources摘要；Web新增/api/jobs只读端点与⑳面板（HTTP级验证）。
- request.json快照不含粘贴正文（全文以sources/为准）。原始资料文件只读，从不回写。
- 验证：254项离线测试；B2浏览器验证仍有效；B3页面只做HTTP/HTML级验证，未人工浏览器点检。
- 限制：URL/搜索（S2-02/03/04/09）与网页转载去重随B4；S2-05/06/07的网页侧与模型工具调用未接通；
  sources/artifacts仍为JSON索引非多文件事务；真实模型业务执行与浏览器人工验收未执行。
- 完整说明见B3_DELIVERY.md，逐项状态见IMPLEMENTATION_TRACKER.md，逐步证据见IMPLEMENTATION_LOG.md。

## B4 URL抓取与网络安全边界（2026-09-09）

- 新增`src/harness/ingest/`：url_policy（协议/格式白名单；私网/回环/链路本地/组播/保留/文档网段拦截，IPv4-mapped按v4；解析集整体校验）、fetcher（http/https直连白名单IP、HTTPS保留server_hostname证书校验、每跳重定向重新parse+resolve、超时/10MB上限/正文类型白名单）、html_extract（HTMLParser正文、标题h1~h6转Markdown、剔除script/style）。
- 来源：SourceRecord 增加 final_url/http_status/content_type；SourceStore.add_url 把抓取层状态映射为存储层分类并登记；网页与本地同文转载去重复用既有判重。
- 入口：TaskRequest.urls/allow_network；新增 src/application/imports.py 统一 texts/files/urls 导入；CLI --import-url、Web 链接输入；url_policy 默认安全、测试与可信 intranet 显式 allowed_hosts。
- 搜索：src/harness/ingest/search.py 占位——SEARCH_PROVIDER 未配置即禁用（可操作提示、不假搜、不用Mock顶替），SearchRecord 预留根任务/费用字段（未知成本显式 None 不记零）；Settings/.env.example 增加 SEARCH_*。
- S2-10：网页文本无执行路径，测试固化（指令原样入来源、不改请求快照、工具注册表不变、目录外无新文件）。
- 验证：330项离线测试（URL策略49/抓取9/URL来源6/整链与S2-10七项/搜索网关4）。
- 限制：无真实搜索服务商（需选定并按官方接口与费用核验）；真实公网抓取质量、DNS rebinding真实样本与代理场景待S6；Web/CLI 未暴露私网白名单开关（默认安全）；S2-11 PDF 独立未做；成稿链 B5 未接入（网页正文仅登记存储）。
- 完整说明见B4_DELIVERY.md，逐项状态见IMPLEMENTATION_TRACKER.md，逐步证据见IMPLEMENTATION_LOG.md。

## B5研究写作链（2026-09-09）

- 新增`src/application/pipeline/`：固定阶段链 证据→素材包→提纲→初稿→双层审校→≤2轮修订；产物全落 ArtifactStore（material_pack/outline/report.vN/review.vN），evidence.json 为证据权威存储，pipeline.json 为阶段快照。
- 程序先行校验：quote 逐字定位后才入库；引用标记 [E-xxx] 必须能解析到证据；提纲必需证据全覆盖；章节齐全；〔事实/推断/未知〕标注（要求节）；模型审校的 accepted 含 error 自动改判。冲突 status 强制 open（无依据消解被拒），重复来源由登记补齐。
- 结果分级 S3-11：accepted→completed；draft（修订耗尽/预算停止/证据或资料缺口）→partial 或 cancelled，final_text 保留草稿；阶段解析失败→failed。修订每轮保存问题清单与新稿版本，旧稿不覆盖。
- 入口：TaskRequest.flow（agent|research）；ResearchApplication 同根账本内跑链（purpose 记账 evidence_extract/material_pack/outline/draft/review）；CLI `--flow research` 端到端；Web 明确提示链接入随 S5。
- 验证：347项离线测试（链上12 + flow接入5）；桩/脚本大脑下全链可复现：两份及以上资料→accepted报告、引用全可定位、修订一次后干净、顽固错误→draft、预算max_calls=1→partial保存、无来源/无证据→明确提示。
- 限制：链在 Mock/桩 LLM 下离线验收；真实模型结构化输出质量、20业务案例执行、人工评分待 S6；整理与写作共用一条固定链（由目标引导）；模型按需读全文/窗口自适应的工具与 S4 会话改稿/追问未接入；Web 只读展示 artifacts（⑳面板），运行入口随 S5。
- 完整说明见B5_DELIVERY.md，逐项状态见IMPLEMENTATION_TRACKER.md，逐步证据见IMPLEMENTATION_LOG.md。

## S4持久化状态、恢复与账本（2026-09-09）

- 新增`src/harness/state/`：state.sqlite（jobs/sessions/session_jobs/approvals/operations + meta.schema_version；WAL、check_same_thread=False+写锁、在线备份、损坏/高版本显式报错）；业务状态9词+迁移表+底层Run映射；队列（原子领取/心跳/过期回收/startup_scan/两段取消）；操作账本（幂等键=job:action:version:params_hash，succeeded才回放、unknown拒绝自动重放）；持久化审批（绑定动作+参数哈希+scope+版本+到期，新审批自动失效旧pending）；会话（目标与任务归属、材料快照不跨会话共享）。
- 链层：runner 阶段检查点 stage_{material,outline,draft}.json，顺序=产物→检查点→外部状态提交；已有检查点目录拒绝无意识重跑；resume=True 从检查点续跑（真实子进程 os._exit 崩溃→续跑 accepted 且证据不重复抽取）；should_stop 阶段边界收敛为 cancelled；control/subprocess_guard.py（有界子进程）与 progress.py（无进展检测）。
- 入口：resume_research_job（同任务续跑+续接账本：原调用载入、次数/输出/费用延续原上限；未知用量如实阻止续跑）；CLI `--resume-job`（真实模式验收；默认 Mock 明确失败不假成功）。
- 修复：Mock 适配器失败=已知零成本（不污染 unknown 账）；真实模式失败仍阻止后续调用。
- 验证：372项离线测试（状态核心14/S4链层与能力8/续跑入口3 等）；真实子进程崩溃恢复含两程执行。
- 限制：常驻队列执行器、Web 提交/取消/审批/会话接线与 Agent循环取消门随 S5（S4-03/05/06/09/11 剩余接线）；跨进程租约压力测试与真实模型验收随 S6；节点级官方 Checkpointer 未接（阶段边界决策）。
- 完整说明见S4_DELIVERY.md，逐项状态见IMPLEMENTATION_TRACKER.md，逐步证据见IMPLEMENTATION_LOG.md。

## S5研究任务工作台（2026-09-09）

- 研究写作链接入 Web：POST /api/runs flow=research 进 SQLite 队列（S4 接线：submit→claim→worker执行→按结果分级 release，领取前已取消直接收敛）；worker 单实例常驻。
- ResearchApplication.run 支持 job_id/on_progress/stage_hook/should_stop 注入；阶段取消在边界收敛；恢复走 resume_research_job（拒绝无检查点与已验收任务）。
- 新端点：/api/jobs（列表）、<id>/progress、<id>/evidence、<id>/artifacts/<aid>/download、<id>/cancel、<id>/resume；页面新增㉑研究任务卡（任务列表/阶段分级/停止/恢复/报告分词引用→证据摘录与定位/版本列表/导出）。
- 写接口安全：Host 回环白名单（403）、请求体≤1MB（413）、仅 application/json（415）、默认绑定127.0.0.1；渲染全走 textContent/DOM，导出 attachment+nosniff，只允许任务内登记产物。
- 验证：378项离线测试；重启后同一 state.sqlite 的任务仍可查可续。
- 限制：agent 流程未进队列；追问改稿/审批失效原因页/读者长度与高级折叠/大日志分页/HTML富预览/浏览器人工验收未做（部分随S6）。
- 完整说明见S5_DELIVERY.md，逐项状态见IMPLEMENTATION_TRACKER.md，逐步证据见IMPLEMENTATION_LOG.md。

## S6真实评测运行器与运维（2026-09-09，离线可验收部分）

- eval/business_eval.py：案例全走统一业务入口；真实模式必须 --max-cost，缺Key整批 not_executed 且零请求；repeats/fault_rounds 可配；失败样本整目录保存；逐次记录耗时/费用/未知用量/失败原因/机器检查（引用/章节/事实/禁语，仅供参考）；报告含数据集/代码/Python/包/模型/工具/脱敏配置快照。
- eval/human_scores.py：逐次人工评分表（不自评分盖章）；src/ops/{health,backup,verify}.py 与 requirements.lock.txt；docs/{TRIAL_LOG_TEMPLATE,BROWSER_REGRESSION}.md 模板。
- 验证：387项离线测试（S6新增9项：real缺Key不执行零请求、stub通过、Mock诚实失败与样本、改稿跳过、评分表、健康/备份roundtrip/verify）。
- 限制（真实执行待用户）：60次真实业务运行、联网冒烟、故障全量两轮、人工评分导入、7天试用、单Agent基线对比、全新venv安装验证、首次Git提交（S0-06）。
- 完整说明见S6_DELIVERY.md，逐项状态见IMPLEMENTATION_TRACKER.md，逐步证据见IMPLEMENTATION_LOG.md。

## S6链内硬约束闸门（2026-09-09，S6-05 对齐）

- 动因：真实批次暴露"链内 accepted ≠ 业务达标"（o08 仅236字、缺任务要求的必需章节，链内仍放行）——链内验收只对照模型自生成的提纲。
- 契约与入口：`HardRequirements`（required_sections/forbidden_claims/key_facts）→ `TaskRequest` 三个同名字段（校验+快照保留，续跑与追问改稿都能还原）→ CLI `--require-section/--forbid-claim/--key-fact`。
- 链内行为：硬要求注入提纲/初稿/审校提示词；提纲缺必需章节时程序补入（记 warn，模型自造结构不得替代任务要求）；正文标题要求逐字一致；程序层新增"必需章节缺失=error、禁语出现=error、关键事实未逐字出现=warn"。
- 读数与对照：`PipelineResult.hard_checks` 与 pipeline.json/job.json 记录必需章节/禁语/关键事实命中；business_eval 把数据集标注传入链，并同时记录 `chain_hard_checks` 与独立机器检查，便于比较链内自检与独立评测。
- 证据：离线 414 项测试（新增9项：读数与严重度、提纲补入/不重复补入、达标 accepted、缺章节两轮修订后仍 draft、禁语阻塞、请求校验与快照、入口透传、续跑不丢硬约束、评测路径 chain_hard_checks）；真实单例复验 o08：闸门后链内 accepted 且必需章节 2/2、禁语 0、关键事实 2/2，独立评测（v4-pro）5/5/5/5 → accept（此前 3 分）。
- 闸门批次首轮（前3例）暴露并已修复"缺标注"**误报**：章节正文取法遇标题即停 + 包含匹配命中大标题，使含子标题章节的正文被判空（链内 draft 而独立评测判 accept）。修复为"精确匹配优先 + 取到下一个同级/更高级标题"，回归测试已加；受影响产物保留为 `eval/reports/gate_buggy_o01..o04`。
- **闸门后真实批次（修复代码，20/20 执行完成）**：链内 accepted 14 / draft 6 / failed 0；独立评测 20/20 打分 = **10 accept / 5 draft / 5 fail**，维度均值 3.85/4.75/3.90/4.65（第一轮 16 例为 1 accept、3.88/2.94/3.69/3.62）；独立机器检查的必需章节命中由 19/20 例为零变为 **20/20 例全命中**；链内 accepted 的独立通过率 **6%→50%**；成本 ≈$1.20（链）+$0.28（评测者）。20 份人工评分表已生成（`eval/reports/gate_real_*/oXX_rep1_scores.csv`）待填写。
- 限制：关键事实仍是逐字命中口径且只记 warn（语义覆盖由评测/人工判定）；必需章节采用"标题归一后包含匹配"，比 eval 机器检查的严格相等口径宽松；本批把数据集关键事实也作为任务要求传入，事实命中率的提升含"提示效应"（结构维度的提升才是纯闸门收益）；单次运行波动明显（o08 单例冒烟 5/5/5/5 accept、本批 3/3/3/3 fail），达标率须以 repeats=3 + 人工确认为准；correctness 均值与上一批持平（3.85），语义质量是下一阶段主攻方向（候选：链内审校换更强模型、按证据分级修订）。

## 方向变更：自适应多智能体（2026-09-10 立项）

- 目标能力（用户确认，产品主线）：**给一个研究主题，系统自己分析需要哪种多智能体模式、是否需要子智能体，然后一步步完成研究**。
- 决策：由**专用调度智能体**读题输出结构化执行方案（模式/子任务/角色/预算/降级/理由，可审计）；子智能体用于并行拆子题、角色分工，**并允许嵌套**（配深度≤2、派生总数与并发上限、同题去重、预算递减四道护栏）；固定流程降级为候选与保底模式。
- 设计文档：`docs/DYNAMIC_ORCHESTRATION_PLAN.md`（含契约草案、模式判据、护栏、入口展示、三层验收口径、S8-01～S8-06 分阶段实施）。`PRACTICAL_RESEARCH_WRITING_PLAN.md` 第 1.4 节与 S7 的相关限制已同步修订。
- 现状与差距：六种协作模式与"经理/子智能体工具"已实现并有离线评测，但**只能人工指定、未接产品入口、从未跑过业务案例**；缺"读题选型层""策略与研究写作产物打通""计划与子智能体可观测"三块。
- 同批纠偏（2026-09-10 复核发现）：① 评测口径改回文档规定（按案例预期成品/草稿/无法完成算一致率，partial 单列）；② 关闭"把评分要点先告诉写作智能体"的开卷做法；③ 禁止断言改语义判定，程序层只提示。三项均为待实施，其中①可用已有产物免费重算。

## 外部审阅采纳与计划收敛（2026-09-10）

- 审阅方式：文档审阅 + 局部代码抽查（未重跑全量测试）；三处代码引用核实属实：评测仍传数据集标注给写作链（`eval/business_eval.py`）、根账本锁在模型请求期间全程持有致同根调用串行（`src/harness/model_gateway.py` `call()`）、搜索网关仍为空占位（`src/harness/ingest/search.py` `SUPPORTED_PROVIDERS=()`）。
- 采纳结论（详见 `DYNAMIC_ORCHESTRATION_PLAN.md` 头部与各节）：保留"用户给主题、系统自动选方式并交付"的产品方向；优先级改为 **评测可信度 → 搜索等资料获取能力 → 预算分配 → 再逐步开放多智能体**。要点：S8-00 立即优先且三指标分离；首版 auto 只在 fixed/fanout 选型、护栏是执行器前置；"交互默认（auto）"与"默认模式（现 fixed）"分开命名；预算补"整任务分配公式"（根剩余−在途预留−成稿预留）；"恢复不重复付费"降为可验证承诺；搜索接入为主题研究前置（首版候选百度千帆搜索，博查/Brave/Tavily 备选，Bing API 已退役不可用）。
- 文档状态同步（本次完成）：验收基线"当前状态"改为指向总账（案例定义不变，执行状态见本文件）；浏览器清单"改稿未接通"更新为已接通待人工验收；跟踪表 S5 行与 S8 拆解表同步最新口径；README 开篇补用户视角的任务说明。

## 下一步

- **执行顺序（2026-09-10 审阅后定稿；同日搜索策略更新：模拟先行；同日批量开发完成①③的开发部分）**：① 同步状态（已完成）+ **S8-00 评测纠偏**（已完成离线落地：口径重算读数、闭卷、语义判定；标注集待人工冻结）；② 整理代表性失败案例（已登记清单：r07 超交付、r03 保守降级缺失、correctness 短板、o08 波动、偶发 workbench 测试）、处理已知偶发测试失败，建立可定位的代码与评测版本锚点；③ ~~模拟搜索~~（mock provider 已完成，搜索→读原文→证据链路接线待做）+ S8-01~04 首版已实现（离线 433 全绿）；④ fixed/fanout 自动选择闭环（CLI 已通，待真实冒烟 + 浏览器验收 + Web 面板）；⑤ 纠偏后口径的小批真实对比（5～6 案例趋势，用数据集资料、不依赖真实搜索），再决定是否扩展 dynamic_team 与嵌套派生。
- **待用户确认的默认值（草案，见设计文档）**：~~方案是否需人工点头~~（2026-09-10 用户已定：交互默认全自动、过程不打扰、交付告知所选方式；「先看计划」为默认关的可选开关）、~~钱闸默认值~~（规则已定：模式目录最省/最贵典型成本取中点的折中，用户显式预算优先；**数值待 S8-05 实测校准**）、关键条件判定清单（**待定**：首版仅"缺了就无法执行"的最小保守清单，随试用反馈扩充）、递归深度 2、派生总数 ≤12、并发 ≤3、子预算 ≤父预算 40%、简单题默认 single/fixed。
- 其余待办：repeats=3 的 60 次批次与 10 故障两轮、人工评分导入（human_confirmed）、S2 搜索服务商、S2-11 PDF、S5 剩余体验项、S7 效率优化、7 天试用。




