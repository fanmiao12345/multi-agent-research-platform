# 执行状态总账（EXECUTION STATUS）

> 本文件是 `DEV_PLAN_LangGraph_Harness_From_Scratch.md` 的落地对照表：
> 每个里程碑/步骤组 → 交付物（代码/测试）→ 证据 → 已知缺口。
> 更新规则：每完成一步优化后刷新测试计数与"最近更新"。

- 最近更新：2026-09-09
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
- 闸门批次首轮（前3例）暴露并已修复"缺标注"**误报**：章节正文取法遇标题即停 + 包含匹配命中大标题，使含子标题章节的正文被判空（链内 draft 而独立评测判 accept）。修复为"精确匹配优先 + 取到下一个同级/更高级标题"，回归测试已加；受影响产物保留为 `eval/reports/gate_buggy_o01..o04`，20 案例批次用修复后代码重跑，结果随后入账。
- 限制：关键事实仍是逐字命中口径且只记 warn（语义覆盖由评测/人工判定）；必需章节采用"标题归一后包含匹配"，比 eval 机器检查的严格相等口径宽松；评测批次把数据集关键事实也作为任务要求传入，事实命中率的提升含"提示效应"，结构（必需章节）部分是纯闸门收益，解释时须分开表述。

## 下一步

S7（按证据优化：有界并行/检索优化/模型路由/可选格式，不阻塞首版）、S2搜索服务商、
Web表单硬约束字段、真实评测扩批（20×3=60次）与10故障两轮、人工评分导入（human_confirmed）、
7天个人试用——真实执行项需 Key/预算/时间，按用户安排推进；S0-06 Git 基线已完成。




