# 执行状态总账（当前主线）

最近更新：2026-09-16。Q1-01～03 已通过：全量离线回归 506、故障矩阵 28/28、真实浏览器与安装恢复完成。Q2-01～03 已完成（含联网真基线成立）；Q3-01 第 1～3 批与 Q3-02 联网专项进行中；R1 简历对齐批次完成（见下）。

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
- D6-06～08：debate、二层嵌套和六模式注册表完成；所有模式共享根预算、来源与结构化成果。
- D7-01～02：引用谱系可回溯原始来源；整理/分析/报告按 delivery_kind 走不同交付路径。
- D7-03～04：明确缺口可触发一次补源并更新素材；来源撤回后可基于剩余资料和最新原稿生成带谱系新版本。
- D8-01～04：状态 schema v3 持久化父子/计划版本/阶段历史与待输入；取消可在分支边界收敛；恢复审计保留来源、阶段、子结果、预算历史和 unknown 操作。
- D9-01～04：CLI/Web 同契约提交，Web 只看计划；工作台显示实际方式、业务进度、待输入/审批/停止恢复状态，并提供来源定位、版本、追问、HTML 与过程记录导出。
- Q1-01：全量离线回归 506 passed。
- Q1-02：14 类故障各 2 轮，28/28 通过。
- Q1-03：真实浏览器 agent/研究旅程、刷新/重启、导出、安装、备份恢复和 MCP 通过。
- Q2-01：**已完成（2026-09-16）**——99 次闭卷批次执行（链内 accepted 80 / draft 13 / unable 2 / 失败或未交付 19；成本 $4.71；引用 958 条零未解析；p50 195s / p95 369s）；人工评分表 94 行已由用户复核采用并 ingest（human_confirmed=true，accept 65/94≈69%，未交付 5 行留空），汇总见 eval/reports/q2_real_batch/business_report_human.json 与 q2_summary。
- Q2-02：**已完成（2026-09-16）**——联网 12 题/六模式覆盖达标；真基线 **3 accepted / 3 draft / 6 unable、0 题预算停止、抓取可读率 56/107 = 52.3%**；旧"0 accepted"读数系评测脚本限额与业务批不一致（8192 token 掐断）造成的**无效基线**，已归档 `q2_web_baseline_invalid_8192.json`。
- Q2-03：**已完成**——`eval/q2_summary.py` 汇总三分离口径（执行完成 96/99=97.0%、预期行为符合 68/96=70.8%、成品质量 49/80=61.3% 独立 / 65/94≈69.1% 人工确认），`ready_for_q3=true`。
- Q3-01（交付等级与内容质量）：第 1 批自述封顶（r03→draft、r07/r12→unable，r04 误伤已修）；第 2 批三项程序检查**用 20 例人工确认批次校准**——`fact_label` 无区分度（人工 accept 的 9 例里 7 例命中）降为 warn，`internal_leak`/`source_count` 保留 error；**第 3 批验证根因修复**：`src_` 内部标识不再泄漏进成品（8 题 0 命中），同 8 题 $0.64→$0.40、5 题由 draft 回到 accepted；**这 8 题新报告需人工再看一次才可算验收**。
- Q3-02（联网获取质量）：诊断出 6/12 题 `unable` 的真因是**搜索相关性**（长句查询退化匹配到泛词，返回词典/日历/软件页）。已修：查询改短关键词 + 结果相关性过滤（提交 `469907e`）；可读率的 35.5% 损失**全是 HTTP 403 反爬**，UA 策略登记 O-14 待用户决策；同集合前后对照复跑待执行（外壳工具暂时不可用，脚本已就绪 `.tmp/run_web_after.py`）。
- 下一步：① 联网 12 题前后对照复跑（Q3-02 验证）；② Q3-01 第 3 批 8 份新报告的人工复核；③ O-14（抓取 UA/反爬）决策后处理可读率。

## 2026-09-11 代码核对基线

| 领域 | 已有基础 | 当前不能声称完成的部分 |
|---|---|---|
| 研究流程 | 来源/PDF 导入、证据、素材、提纲、报告、审校、改稿 | 完整多方式交付与整体业务验收未完成 |
| 入口 | CLI 与 Web 可执行任务；CLI 有初版 auto | Web 仍固定；完整多模式参数与自动选型尚未统一 |
| 搜索 | bing_scrape、候选元数据、正文来源、PDF 与共享来源库 | 真实抓取受反爬/结构变化影响；site/since 尚未暴露到 CLI/Web |
| 协作 | 六模式全部注册，fanout 真并发，dynamic_team/debate 已接入，二层嵌套受控 | 六模式质量/成本比较留 Q2/Q3 |
| 账本与证据 | 根账本、证据定位、根共享来源版本/撤回/下游引用 | 子报告到原始证据的完整映射在 D7 继续 |
| 运行与 UI | SQLite、队列、阶段恢复、版本和部分工作台 | 根/子取消恢复、持久审批和自动模式展示需要贯通 |

本次 D3-04/05 + D4 + D5 联合批次验证：259 passed（PDF、共享来源、Context、Skill、Memory、Runtime、搜索、导入、研究链、规划/重规划与编排）。Q1-01：506 passed；Q1-02：28/28 passed；Q1-03：真实浏览器与安装恢复通过。以上均不是全量结果；未重跑历史 433/434/437 批次。

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

## 下一步（2026-09-11 主计划生效后重排）

以上"执行顺序"已由 [PROJECT_MASTER_PLAN.md](PROJECT_MASTER_PLAN.md) 取代：**D0～D10 功能开发与 Q1 完整功能/可靠性测试已完成**（对照见 `FEATURE_FREEZE.md` 的 G01～G15 与 `eval/reports/freeze_manifest.json`），当前进入 **Q2 真实业务与协作基线**（按 `docs/Q2_Q4_USER_RUNBOOK.md` 执行）：

- **Q2-01**：33 案例 × 3 = 99 次真实运行（v1 冻结 20 例的 60 次在报告内单列），闭卷口径；产出 `eval/reports/q2_real_batch/business_report.json` 与人工评分工作台 → 填写后 `scripts/q2_ingest.ps1` 落定。
- **Q2-02**：≥10 个真实联网主题，六种方式各 ≥2 个适用案例（`scripts/q2_web.ps1`），真实抓取正文与引用谱系写独立报告。
- **Q2-03**：`python -m eval.q2_summary` 汇总质量/费用/时延/人工修改时间；人工未确认时 `ready_for_q3=false`。
- 之后 **Q3** 按基线集中优化（`scripts/q3_compare.ps1` 前后对比、优化项入 `OPTIMIZATION_BACKLOG.md`），**Q4** 连续 7 天试用与最终签收。
- **本轮补充（2026-09-11）**：`eval.business_eval` 新增**整批累计费用上限** `--batch-max-cost`（O-09：原 `--max-cost` 实为单任务上限，报告文字误写"批次限额"，`scripts/q2_real.ps1 -MaxCost 5` 无法封顶整批花费；新增上限用尽后剩余尝试记 `not_executed`，未知用量亦停止整批），Q2-01 建议同时给出单任务上限与整批上限。
- 质量类问题（自造"开放冲突"、推断标成〔事实〕、内部标识泄漏、改稿漏项等 5 类）已登记为 `OPTIMIZATION_BACKLOG.md` 的 **O-08**，按纪律在 Q3-01 集中处理，不在 Q2 基线前改动。

## Q2-01 真实业务基线（2026-09-16，99 次闭卷，v1+v2 全案例）

- 执行：33 案例 × 3 = **99 次**真实运行（v1 冻结 20 例 60 次 + v2 新增 13 例 39 次），闭卷；单任务上限 $0.25、整批上限 $6；**用时 6.05 小时、花费 $4.71、0 未执行、0 未知用量**；10 类故障 × 2 轮（自动探针通过、其余 manual）。
- 三项分离读数（`eval/reports/q2_rescore_graded.md`）：**执行完成率 96/99 = 97.0%**；**预期行为符合率 68/96 = 70.8%**（门槛 90% → 未达标）；**成品质量达标率 49/80 = 61.3%**（链内 accepted 中独立评测 accept）；引用可定位率 **100%**（958 条引用、0 未解析）；端到端时延 mean 220s / p50 195s / p95 369s。
- 独立评测（v4-pro，99/99 打分）：accept 51/99；链内 accepted 四维均值 4.15 / 4.80 / 4.24 / 4.45；伪造标记 1 例 1 条；禁语字面命中 4 次（o12，语义判定未做）。
- 分批次：v1 符合 42/57 = 73.7%、质量 61.7%；v2 符合 26/39 = 66.7%、质量 60.6%。稳定性：33 例中 23 例三次交付等级一致、10 例不一致。
- 最弱机制（Q3-01 优先序，详见优化清单 O-10）：refuse_without_evidence **0/6**、conservative_grading **1/15**、dedup 3/9、gap_declaration 7/18。
- 人工评分工作台 `eval/reports/q2_human_workbench/combined_scores.csv`：**已由用户复核并整体采用 AI 预填分**（口径记录见 `IMPLEMENTATION_LOG.md` 2026-09-11 两条），`ingest` 后 **human_confirmed=true**：**人工 accept 65/94 ≈ 69.1%**；链内 accepted 中人工 accept **57/80 = 71.3%**（人工口径的成品质量达标率；独立评测口径为 49/80 = 61.3%）。
- 本轮工具修复：`eval/grader.py` 单条失败不再丢整批（逐条捕获 + 增量写盘；本次 90 条从日志恢复、9 条补跑）；`eval/q2_summary.py` 缺输入崩溃修复。

## Q2-02 联网与六模式 + Q2-03 汇总（2026-09-16）

- **Q2-02（读数作废，重跑中）**：12 个公开主题（六模式各 2）虽全部执行、覆盖达标，但复核查明 **11/12 题是被 CLI 默认输出上限 8192 token 掐断**（`budget_exceeded/output_token_limit`，47–59 秒），根因是 `eval/web_baseline.py` 未传 `--max-calls/--max-output-tokens/--max-seconds`（业务批为 60/200000/1500）。因此"0 accepted / 可读率 54.7% / 引用谱系 0 / $0.13"**不能当作联网研究质量基线**；旧报告归档为 `eval/reports/q2_web_baseline_invalid_8192.json`。**唯一跑完的 w01** 因搜到无关页面而诚实交付 `unable` —— O-11 的真问题是搜索相关性与正文可读率。限额已对齐，**真基线重跑中**（≈$0.6）。
- **Q2-03 汇总**：`eval/reports/q2_summary.json/.md`（`ready_for_q3=true`）；本次修正 `failed` 口径为互斥分级（原口径把 13 草稿 + 2 无法完成也算进 failed，合计 114>99），新增 `non_accepted` 与联网段 `level_distribution/budget_stopped/baseline_valid`。
- 本轮工具修复（`eval/web_baseline.py`）：① 子进程中文输出 GBK→UTF-8 容错解码（首次运行因此崩溃、前两题作废）；② 每题后增量写盘 + 逐题进度打印 + 单题异常不拖垮整批；③ 覆盖口径修正为"实际执行过即计入"（原口径只算 exit 0，导致六模式覆盖恒为 0）；④ **限额与业务批对齐**（本次纠错的核心）。
- 缺口：`revised_minutes` 全列为 0（预填时无真人改稿时间）→ "人工改稿时间"指标暂缺；补填后可免费重跑 `q2_ingest`。

## Q3-01 进展（2026-09-16）：联网真基线成立 + 第 1 批（自述封顶）

- **联网真基线（限额对齐后重跑 12 题）**：交付 **3 accepted / 3 draft / 6 unable**、**0 题预算停止**；抓取可读率 **56/107 = 52.3%**；引用谱系仍 0（按 D7 缺口处理）。6 个 `unable` 核实为诚实拒绝（页面与主题不匹配/均为宣传与词典类）→ 真问题是**搜索相关性 + 正文可读率**（O-11）。`q2_summary` 刷新后 `ready_for_q3=true`、`baseline_valid=true`，业务分级口径修正为互斥（80+13+2+4=99）。
- **第 1 批（该拒/该降级却交成品）**：诊断确认**阈值信号不可用**（缺口数/事实占比/局限声明比例两组几乎相同），可用依据是**报告自述**。修复 `review.delivery_cap()`：自述做不了核心交付 → `unable`；自述"不足以批准/通过/采购/上线"等正式决定类不足 → `draft`；普通局限与建议类不封顶。
- **前后对比（8 题 ×1，$0.38）**：r03→draft ✓、r07→unable ✓、r12→unable ✓；**r04 误伤已修**（"建议暂不扩大"被误判，收窄口径后恢复 accepted ✓）；o13 未修；r11 遇审校非 JSON 输出 → `failed`（稳定性问题，登记 O-13）；对照 o01 本次 draft 属运行波动、r02 accepted ✓。
- 提交：限额与口径修复 `f432d57`、自述封顶 `5eed4ba`、封顶收窄 `（见最新提交）`。测试：pipeline 24 项 + 相关套件 50 项通过。

## R1 简历技术点补齐批次（2026-09-16）

- 用户要求按简历表述核对并补齐项目。逐条对照（简历→代码落点→实测读数→诚实边界）见 **`docs/RESUME_PARITY_PLAN.md`**。
- 新增：EventBus（六编排模式接线 + Trace 桥）、IterationBudget+熔断（接入子智能体工具）、Skill 四态生命周期与 depends 依赖解析（Runtime 接线）、references 第三层渐进加载、三层记忆门面（Working/Episodic/Semantic）+ 向量索引（sqlite-vss 适配 + 零依赖哈希余弦回退）+ 遗忘曲线衰减、MemoryProvider 与记忆注入 User Message（`memory_in_user_message` 开关，默认 False 保持 Q2 基线）、`eval/drift.py` 漂移检测与阈值告警、FastAPI 适配层 + React 前端页（可选依赖）、`eval/resume_metrics.py` 量化实测。
- 实测读数（离线桩，多实验均值口径，`eval/reports/resume_metrics/`）：Skill 渐进加载 **token 节省均值 81.6%**（17 技能全枚举实验，区间 78.7%~84.8%）；fanout(3 并发) **耗时降低均值 66.5%**（100 轮计时，2.99x）；跨会话语义检索（真实 sqlite_vss 后端 + BM25/IDF 混合重排 + top×5 过召回）**top-3 命中 74%**（100 用例×100 干扰知识，2000 次 bootstrap，95% CI 65%~82%，±8.5）/ top-1 60%。
- 可选依赖实装核验（2026-09-16 追加）：安装 fastapi/uvicorn/httpx/sqlite-vec 后**577 项测试 0 失败 0 跳过全绿**（此前 skip 的 FastAPI 5 项与 vss 后端一致性测试转真跑）；顺手修复 vss 后端 L2→余弦换算 bug（cos=1−L2²/2）与适配层状态大小写两处问题。
- 评测闭环三项补齐（2026-09-16 追加，应用户"核心特性"清单核对）：① `drift.detect_trend()` 滑动均值趋势检测（z 分数离群 + 连续单边漂移双通道，CLI `--history`）；② `eval/experiment.py` A/B 对照运行器 + **零依赖 Welch's t 检验**（正则化不完全 Beta 连分式算 p 值，对教科书参考值校验）；③ `eval/auto_optimizer.py` 护栏版参数反馈闭环（白名单参数/硬边界/每轮 ≤3 项/同参数单次/默认 dry_run + 审计日志，费用上限不在白名单）。补齐后**601 项测试 0 失败 0 跳过**。评测名称映射表见 RESUME_PARITY_PLAN 第五节。
- B/C 类差距闭环（2026-09-18 追加）：① React 页浏览器级验收通过（Playwright 1.63 + Chromium 153 入 dev 依赖；vendor 本地化 + `/vendor` 路由；揪出并修复 vendor 404 与 useState 解构 2 个真 bug；截图留证）；② Prompt Cache 实测口径 4 落地——诚实读数增益≈0，根因登记 **O-15**（compose_context 归一化挤占 messages 份额、历史窗口恒 2 条，行为变更待与 Q3 批次错峰修复）；③ 提速异质抖动场景 59.4%（2.5x）稳健；④ AutoOptimizer 对 Q2 真实汇总 dry-run 演示（p95 正确触发、质量规则正确不触发）。全量 **605 项 / 0 失败 / 0 跳过**（含 2 项真实浏览器测试）。
- 用户三项决策落地（2026-09-18 追加）：① **O-14 选 B 方案已实施**——`site_policy.py` 域名降权换源（静态证据基线名单可配置 + 任务内 403 动态实录），UA 保持自报家门；② **O-15 就绪开关** `reserve_message_window`（默认关）——收益实测开关对照 **+28.9pp**（user 71.3% vs system 42.4%），切换待 Q3 批次收尾；③ **O-11 双臂复跑完成、已关闭**：A 臂（纯相关性修复）accepted 3→**5**、可读率 52.3%→**62.5%**、抓取量 -25%，修复确认有效；B 臂（+降权）可读率 **68.8%** 三臂最高（达设计目标），成稿增益未证实（3 vs 5，波动范围内）。真基线曾被首跑覆盖，已从 git（b97be05）完整恢复。全量 **616 项 / 0 失败 / 0 跳过**。
- 两项挂起任务完成（2026-09-20）：① **B 臂定案**——四轮交错再补 48 题（每臂 36 题），accepted 率 B 30.6% vs A 27.8%，**p=0.795 无显著差异 → 成稿质量等价，B 方案定案保留为默认**（可读率略优、少抓 403 站）；单轮波动实锤（轮间差 3 题 > 臂间差 1 题）。② **O-15 已切换默认 True**——切换后全量 616 项回归通过，真实冒烟一题联网研究 accepted（6/8 来源、206s）；回退口=置 False。证据：`eval/reports/q3_web_six_sample_summary.json`、`q3_web_o15_smoke.json`。
- 第 5 批 O-15 后基线重测（2026-09-20 追加）：12 题（三类覆盖+弱点机制案例）符合率 **10/12=83%**（Q2-01 基线 70.8%，方向性）；r07 自述封顶仍工作、r01/r08 改善（dedup 弱点在长窗口下好转）；发现并修复 **O-15 配套回归**——evidence 恢复比例份额后大素材包被截断（v01 unable），`context_budget` 6000→**12000** 后 v01 重跑 accepted，全量 617 项回归通过；o13 封顶摆动（漏报→过头）记 O-10/O-12。**预算建议案** `docs/BUDGET_CALIBRATION.md`：max_cost None→$0.15、max_seconds 300→600 待拍板，context_budget 已随回归修复切换。
- 第 6 批覆盖补全（2026-09-20 追加）：v1 冻结 20 例在 O-15 + 12000 新配置下**全覆盖**，预期符合率 **18/20 = 90%，达总计划 8.1 验收门槛**（基线 70.8%，+19.2pp）；等级 16 accepted/3 draft/1 unable；两个失配均为保守方向（accepted→draft，人工判定方向一致）；gap_declaration 39%→85%、dedup 改善（r08 accepted）、refuse/conservative 验证通过；合计成本 $1.09。Q3-01 关闭评估：门槛首证达成，建议抽样复验（×3）或用户接受单轮关闭；开放记录项：④ 反事实断言、o08 漏报、o13 封顶边界细化。
- 第 7 批关闭复验（2026-09-20 追加，用户选项 A）：6 弱机制/关键例 ×3=18 次真实运行，合并新配置全部 35 次运行符合率 **89%——贴线达标**（单轮口径 90%）。r03/r07 4/4 稳定、r04/r08 3/4、o03 2/4 摆动；全部失配均为保守方向。Q3-01 关闭评估已出：建议接受贴线达标关闭（四项已知限制带入 Q3-03/Q4），待用户确认。
- **Q3-01 已关闭（2026-09-20，用户确认选项 A）**：以新配置 35 次真实运行 89%（单轮口径 90%）贴线达验收门槛关闭；四项已知限制带入 Q3-03/Q4 继续观察：o03 摆动（50%）、o13 封顶边界细化、④ 反事实断言（语义判定）、o08 漏报。**进入 Q3-03：完整回归 + 真实复验**（真实复验批次待预算默认值拍板后启动，以最终出厂配置为准）。
- **Q3-03 已完成（2026-09-20）**：① 全量回归 617 项 0 失败；② 真实复验 8 例困难子集 62%（三失配=两保守一摆动）；③ **Q3 收官数字（诚实口径）**：预期符合率合并 79%（58 次全部真实运行）/ v1-20 单轮 90%——单轮达 90% 门槛、合并未稳定达成，差距来自单例波动与困难子集；失配性质安全（危险失配 0、引用可定位 100%）。**Q3 阶段关闭，进入 Q4（7 天个人试用，20 个真实工作任务）**；剩余限制 8 项如实带入（见 IMPLEMENTATION_LOG Q3 收官条目）。
- 界面收敛与更名（2026-09-22，用户要求）：**界面只保留标准库工作台**，FastAPI/React 演示层整体移除出仓库（Playwright 工具链保留）；README 标题更名「多 Agent 协作智能研究平台」不再使用 MVP 字样；面试手册移出仓库并重写历史（见下）。
- 验证：新增 50 项测试；**全量回归 571 passed / 1 skipped / 0 failed**（3 个无关测试首跑失败为 Windows 高负载偶发，隔离复跑全过）。
- 诚实边界：简历"85% 完成率"无出处（真实三分离读数 97.0%/70.8%/61.3~71.3%，见 Q2-01）；sqlite-vss/FastAPI/React 均为可选层；默认运行行为与 Q2 冻结基线一致。





