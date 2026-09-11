# 实施日志

本文件追加记录完成步骤；已有历史证据保留在EXECUTION_STATUS.md与B1_BASELINE.json。

## 2026-09-09 / P00：完整计划梳理

- 已核对完整S0～S7计划和当前模型、Runtime、Web、评测、规划与编排调用点。
- 新增IMPLEMENTATION_TRACKER.md，逐项保留原计划编号；B1完成范围和真实业务未验收分别标注。
- 发现B2主要缺口：各入口单独装配Runtime，辅助LLM调用绕过run用量；线程分支也需继承同一根任务。
- 验证：清单编号从主计划提取，覆盖全部编号项；旧174项为B1历史基线，本批稍后重新执行回归。
- 下一步：B2-01统一请求和应用入口，随后接根账本；不在本批宣称已能读取资料或写出有引用的报告。

## 2026-09-09 / B2-01a：统一请求契约

- 新增src/application/request.py：明确任务、模式、档案与四类根限制，兼容显式force_mock。
- 验证：.venv\Scripts\python -m pytest tests/test_application.py，8 passed。
- 零预算合法，负数、NaN、布尔型次数及冲突模式均拒绝。应用执行入口待接账本后完成。

## 2026-09-09 / B2-01：统一应用执行入口

- 新增ResearchApplication：为每次提交创建jobs/job_id，统一请求、Runtime装配和终态保存；run保持原目录兼容历史查看。
- 实际适配器模式必须与请求一致；run.json和返回值记录root_job_id。
- 验证：test_application.py与test_model_config.py共40 passed，包含应用计算、运行关联、错误模式拦截。
- 限制：入口当前执行现有Runtime，资料业务字段与成稿链在B3～B5实现。

## 2026-09-09 / B2-02：根账本与初始限制

- 新增model_gateway.py：请求前原子记录调用意图，记录角色/用途/实际模型/用量/耗时；不保存消息与供应商响应正文。
- 根限制涵盖调用次数、输出Token、估算费用和时间；真实模式未指定费用阈值时默认0.05美元，Mock默认不限制费用。
- 同根模型请求串行检查；缺用量或失败后的未知消耗阻止后续调用，未知价格不能在费用限制下请求。SDK隐藏重试关闭。
- 验证：同上40 passed，其中覆盖四类零限额、未知用量、未知价格、线程共享次数、写盘失败前不发请求、已关闭任务不可再请求。
- 限制：参考价格未实时核验；费用不是账单硬封顶；时间是调用边界检查，后台工具不能强杀。恢复账本仍待S4。

## 2026-09-09 / B2-03：调用点与线程上下文接入

- 主循环、Planner、Replanner、Judge、技能Rerank、历史摘要统一调用model_call；预算停止不能被普通异常兜底吞掉。
- Worker/子Agent记录角色、根任务和父run；Fanout/Debate/Dynamic及工具等待线程复制根任务上下文。
- 定向回归93 passed：辅助调用同账本、并发共享上限、工具线程父子关联、适配器收到剩余输出/时间限制。
- 委派工具的角色标记补充随最终全量回归验证；这些实验策略没有自动加入当前研究业务链。

## 2026-09-09 / B2-04：三个入口与用量展示

- Web、CLI、Agent Benchmark共用ResearchApplication.run；评测批次从根账本累计费用，保留历史run目录与接口。
- 新增python -m src.interfaces.cli命令；Web新增限额输入和/api/runs/id/job账本视图。
- 定向回归93 passed包含真实CLI子进程计算42、HTTP零调用上限与根账本、模型配置和评测边界。
- 页面实际浏览器验证留在B2-05；取消队列、SQLite持久恢复与会话暂未实现。

## 2026-09-09 / B2-05：最终验证与交付

- 全量回归：.venv\Scripts\python -m pytest，199 passed，16.98秒；B2-03的最终子委派角色补充也已覆盖。
- 离线Agent Benchmark：5通过、8明确跳过。业务定义校验：20任务+10故障，definition_valid=true，business_executed=0。
- 本地浏览器：调用上限0→cancelled、0次调用；恢复12→计算42、completed、根账本2次调用；页面最终状态与账本一致。
- 新增B2_DELIVERY.md；同步README、EXECUTION_STATUS、总计划及72项清单；AGENTS记录了后续每步必须追加日志的约定。
- 没有付费模型请求、没有新增第三方依赖、没有修改DSH或全局配置；没有批量Git提交。
- 已知限制：无受控全局队列/持久取消；时间非强制终止，费用为旧参考估算；JSON非多文件事务；实际研究写作仍未验收。
- 下一步B3：文本/TXT/Markdown导入，来源/完整产物、去重、原文定位及路径边界。

## 2026-09-09 / B3-00：B2全量回归与偶发修复

- 复核B2后基线：198 passed + 1 偶发失败（test_http_root_budget_and_ledger_view，单独重跑3次全过）。
- 根因：/job 视图在 run.json 尚未写入 root_job_id 的窗口返回 {"note":…}，轮询谓词对 None 直接抛 AttributeError。
- 修复：tests/test_workbench.py 谓词改为 (d.get("ledger") or {}).get("status")，容忍启动窗口；接口语义未变。
- 验证：修复后定向测试通过；全量随B3批末回归。
- 下一步：B3-01 路径边界工具。

## 2026-09-09 / B3-01：受控路径边界（S2-08本地部分）

- 新增 src/harness/storage/paths.py：canonical/_resolve_through_links/is_under/ensure_under/resolve_under/ensure_relative_name。
- Windows junction 实测发现 os.path.realpath 对“最终段不存在”的路径不解析中间 junction，逃逸检查会被绕过；改为“最长已存在前缀解析 + 不存尾部拼接”再判定包含性，对将写入的新路径同样生效。
- 校验输入白名单（单段文件名、禁 ../、绝对路径越界、root 自身）；全部写路径先通过 resolve_under。
- 验证：tests/test_storage_paths.py 11 passed（含真实 mklink /J junction 逃逸拒绝、不存在尾部穿越 junction 拒绝）。
- 限制：broken junction（目标已删）按普通名处理，只在 OS 写入时报错；URL 侧私网边界属 B4。
- 下一步：B3-02 来源存储与导入。

## 2026-09-09 / B3-02：来源登记、导入分类、定位与去重（S2-01/S2-05/S2-06本地部分）

- 新增 src/harness/storage/sources.py：SourceRecord/索引/全文/meta；job_dir/sources/ + sources.json 布局。
- 分类：ok/partial/duplicate/empty/unsupported/too_large/read_failed 全部登记索引；文件只读不回写；UTF-8→GB18030→替换式降级（partial）；扩展名黑名单（PDF 提示 S2-11、DOCX 等）+ 二进制嗅探先于文本解码。
- S2-05：source_id/导入时间/内容哈希/规范化哈希/原始地址/存储相对路径/published_date=unknown 不冒充。
- S2-06：Markdown 标题+段落切分，heading/paragraph/字符偏移定位；标题前无空行先收拢段落；重复识别=同 sha256 或空白折叠一致（转载），duplicate_of 指向原来源且不重复存全文。
- 限制：单来源2MB/任务20来源/累计10MB 超限明确拒绝不静默截断（累计总量在 import_texts_and_files 内逐条核算）。
- 修复过程中发现并处理：带前缀文件名误过 ensure_relative_name（先验裸名再拼前缀）、heading 正则未考虑多行与“.”不匹配换行、NUL 字节为合法 UTF-8 导致 .txt 二进制漏检。
- 验证：tests/test_sources.py 13 passed（含累计总量上限）；tests/test_storage_paths.py 11 passed 仍绿。
- 下一步：B3-03 受控产物(Artifact)存储。

## 2026-09-09 / B3-03：受控产物存储（S2-07）

- 新增 src/harness/storage/artifacts.py：ArtifactStore 按 job 管理 artifacts/<kind>.v<n>.<ext> + artifacts.json 索引。
- artifact_id=kind.vN 版本自动递增，同 kind 永远不覆盖旧版本；content_hash 写入索引，读取时校验内容与索引一致；索引损坏/重复 id 一律拒绝继续写入与读取。
- 读取入口两种：read(artifact_id) 只认索引；read_relative() 只接受 artifacts/ 下受控相对路径，绝对路径与任何 ../ 按路径边界拒绝，未登记文件拒绝读取（索引是权威，为 B4/B5 模型访问工具预留）。
- 索引先文件后登记方向与来源模块一致：先原子写全文，再写索引，杜绝登记不存在的产物。
- 验证：tests/test_artifacts.py 12 passed（版本/防覆盖/哈希篡改/越界/孤儿文件/损坏索引）。
- 下一步：B3-04 任务请求携带资料字段并接入 ResearchApplication/CLI。

## 2026-09-09 / B3-04：请求契约与统一入口导入接入

- request.py：TaskRequest 增加 texts（粘贴正文）/files（本地文件路径）元组字段，JSON 列表自动转元组；类型/数量校验；snapshot() 落盘快照排除粘贴正文（全文以 sources/ 为准）。
- model_gateway.py：request.json 使用 request.snapshot()（无则 asdict），避免正文双份存储。
- research.py：run() 在启动 Runtime 前导入资料：0 个可用来源抛 SourceImportError（明确消息、job 记 failed），部分失败登记在 sources.json 并继续，job.json 增加 import 摘要（total/usable/statuses）。
- cli.py：新增 --import-file/--import-text（可重复），stdout JSON 附带 sources 摘要；导入类业务错误打印完整 message（不再只给类型）；零可用来源退出码 1。
- 验证：tests/test_imports.py 14 passed；tests/test_application.py 25 passed 无回归。
- 限制：资料只登记存储，业务阅读/成稿链仍在 B5；任务间无持久会话隔离（S4）。
- 下一步：B3-05 Web 查看资料与产物。

## 2026-09-09 / B3-05：Web 资料/产物只读端点与页面

- workbench.py：/api/jobs/<job_id>/sources、/sources/<sid>/text、/artifacts、/artifacts/<aid>/content；job_id/source_id/artifact_id 白名单正则，全文读取一律经 resolve_under 边界校验；GET 不做 mkdir 副作用。
- 页面新增⑳资料与产物面板：任务带资料运行后列出来源（状态/名称/标题/字节/说明，重复与失败来源标明原因，无全文来源点击提示404原因）与产物清单，点击行查看全文/内容；同一 job 只加载一次。
- 验证：tests/test_workbench_b3.py 5 passed；全量回归 254 passed，23.11秒（B2基线199）。
- 限制：只做 HTTP/HTML 级验证，未做真实浏览器人工点检；资料段落明细存 <id>.meta.json，不在列表接口膨胀返回；任务资料全文仅在来源目录，job 页面不发送给模型（发送范围以配置提示为准，B5 成稿链再明确上下文组装）。
- 下一步：B3_DELIVERY.md 交付记录 + TRACKER/README/EXECUTION_STATUS 同步；随后进入 B4（URL/搜索）或按用户优先级调整。

## 2026-09-09 / B4-01：URL 策略与 SSRF/私网防护（S2-09 离线部分）

- 新增 src/harness/ingest/url_policy.py：UrlPolicy（allowed_hosts 白名单/重定向上限/超时/下载上限）、parse_url（仅 http/https、禁 userinfo、IDNA/端口校验、默认端口补齐）、resolve_allowed（解析结果逐地址检查，任何私网/回环/链路本地/组播/保留/文档网段整体拒绝；IPv4-mapped IPv6 按 IPv4 判定）。
- 与 fetcher 分工：本模块保证“解析集合法”；最后防线（直连解析出的 IP+连接后 peer 校验）在 fetcher。
- 验证：tests/test_url_policy.py 49 passed（非法协议/格式、全套私网与保留地址、mapped 形式、名字与 IP 原文白名单放行、混合解析拒绝、DNS 失败）。
- 限制：代理/证书以外的攻击面与真实公网服务验证待 S6；默认拒绝 intranet 材料，需要时须显式白名单（后续 S5 暴露配置）。
- 下一步：B4-02 抓取器与 HTML 正文提取。

## 2026-09-09 / B4-02：抓取器与正文提取（S2-02）

- 新增 src/harness/ingest/fetcher.py：http.client 直连白名单 IP（HTTPS 用 server_hostname 保留证书校验）；每跳重定向重新 parse+resolve；连接/读取超时、下载字节上限超限即断、正文类 Content-Type 白名单；状态分类 ok/http_error/timeout/network_error/too_large/unsupported_type/redirect_limit。
- 新增 src/harness/ingest/html_extract.py：标准库 HTMLParser 提取 title/段落/标题（h1-h6→Markdown #），剔除 script/style 等；text/html、xhtml、text/plain、markdown 内容类型 + 无类型嗅探；charset 声明/GB18030 兼容。
- 验证：tests/test_fetcher.py 9 passed（提取质量/跳转复检/重定向环/404/图片类型拒绝/超限/默认策略拦截回环/协议与 DNS 错误）。
- 限制：不渲染 JS/复杂表格；断连重试不做（网络层错误按来源登记失败并提示）；解析质量在 S6 用真实样本评估。
- 下一步：B4-03 SourceStore 登记 URL 来源（kind=url、原始/最终URL、跨来源转载去重）。

## 2026-09-09 / B4-03：SourceStore 登记 URL 来源

- SourceRecord 增加 final_url/http_status/content_type（URL侧S2-05：原始URL在 original_address，重定向后URL在 final_url，published_date=unknown 不冒充）；索引与 meta 同步扩展（旧索引可读）。
- 新增 SourceStore.add_url：抓取层状态映射为存储层分类（http_error/timeout/network_error/redirect_limit→read_failed；unsupported_type→unsupported）；ok/partial 存提取正文（跨本地/网页同文去重复用既有哈希判重，转载只留 duplicate_of）；空正文/超限明确分类。
- 验证：tests/test_url_sources.py 6 passed；tests/test_sources.py 13 passed 无回归。
- 下一步：B4-04 请求/应用/CLI/Web 接入与整链测试。

## 2026-09-09 / B4-04：请求/应用/CLI/Web 接入 URL 导入

- request.py：TaskRequest.urls（用户指定http(s)链接）+ allow_network（联网研究开关，搜索未配置时无效果）；空粘贴文本仍允许（导入器分类 empty），文件/URL 条目必须非空；总数上限含 urls。
- 新增 src/application/imports.py：import_request_sources 统一导入 texts/files/urls：URL 走 fetch_url→extract_document→store.add_url，抓取失败逐条登记；累计总量逐条核算；零可用来源明确失败。research.py 改用该入口并接受 url_policy 注入（默认安全策略）。
- cli.py：--import-url（可重复）与 --allow-network；Web 表单增加网页链接输入并随 startRun 发送。
- 修正请求校验对空粘贴的误伤（保留 B3 的 empty 分类语义）。
- 验证：tests/test_url_imports.py 7 passed（含 S2-10 网页指令惰性结构保证：文本原样入来源、不改系统指令/请求快照、工具注册表不变、任务目录外无新文件）。
- 限制：Web/CLI 不暴露私网白名单开关（默认安全）；搜索引擎仍禁用；成稿链未接入，网页正文只是登记存储。
- 下一步：B4-05 搜索占位网关与账本字段。

## 2026-09-09 / B4-05：搜索服务占位（S2-03/04）与S2-10证据

- 新增 src/harness/ingest/search.py：SEARCH_PROVIDER 未配置 → SearchNotConfigured 可操作提示（绝不假搜/不用Mock顶替）；未实现服务商显式报"尚未接入"；SearchRecord 预留根任务/排名/URL/费用字段，未知成本保持 None 不记零（S4-10 记账接口已对接字段）。
- Settings 增加 SEARCH_PROVIDER/SEARCH_API_KEY(repr隐藏)/SEARCH_BASE_URL/SEARCH_MAX_RESULTS；.env.example 同步并注明选定服务商后才接入。
- S2-10：网页资料作为不可信内容，目前无任何执行路径；以测试固化结构保证（见 B4-04 日志条目），B5 成稿链喂给模型时再按真实语义复验。
- 验证：tests/test_search_gate.py 4 passed。
- 限制：无真实搜索服务（需用户选定 provider 并按官方接口/费用核验）；真实模型行为验证待 S6。
- 下一步：B4-06 全量回归与交付文档。

## 2026-09-09 / B4-06：最终验证与交付

- 全量回归：.venv\Scripts\python -m pytest，330 passed，32.64秒（B3基线255）。
- 新增 tests/test_url_policy.py 49、test_fetcher.py 9、test_url_sources.py 6、test_url_imports.py 7、test_search_gate.py 4，共75项。
- 新增 docs/B4_DELIVERY.md；同步 README、EXECUTION_STATUS、IMPLEMENTATION_TRACKER（S2-02/05/06/09/10 已验收（离线），S2-03/04 部分完成注明待选服务商；B4 拆解表）与 .env.example。
- 没有付费模型/搜索请求、没有新增第三方依赖、没有修改 DSH；页面为 HTTP/HTML 级验证。
- 已知限制：搜索服务商待选定；真实公网抓取质量与 DNS rebinding 真实样本待 S6；成稿链 B5 未接入。
- 下一步 B5：证据→素材→提纲→初稿→审校，把登记资料接进研究写作链。

## 2026-09-09 / B5-01~B5-07：研究写作链（S3）主体实现

- B5-01 契约：新增 src/application/pipeline/{__init__,model,evidence,prompts}.py —— Evidence/MaterialPack/OutlineSection/ReviewIssue/PipelineResult 数据契约；evidence.json 权威存储；引用标记 [E-xxx] 与稳定编号 E-001…；提示词模板集中在 prompts.py（统一红线：只用给定资料、quote 逐字、禁编造 id、JSON 机器校验）。
- B5-02 上下文预算（S3-07）：单来源提取调用 40k 字符预算并显式标注截断，禁止猜测未提供后段；素材/提纲块 20k 上限；证据索引始终带 evidence_id+fact+来源，列表摘要不作为唯一业务输入（S3-02 由 artifact_id 交接实现：每阶段产物落 ArtifactStore，report.v1→修订 report.v2 不覆盖）。
- B5-03 证据与素材（S3-04/05）：程序先校验 quote 逐字存在于全文并计算段落定位（与 split_segments 同口径）再入库，未定位摘录丢弃记 error issue；素材包校验 evidence_id 存在性，冲突 status 强制 open（模型"解决"矛盾被拒绝并记 issue），重复来源由程序从登记补齐。
- B5-04 提纲与初稿（S3-06）：章节 required_evidence 只保留真实 id；初稿产出 Markdown 并在 [E-编号] 后标注〔事实/推断/未知〕（要求节）；空/超短稿按阶段错误处理。
- B5-05 双层审校（S3-09/10）：程序层校验引用可解析/必需证据全覆盖/章节齐全/标注存在；模型层检查 support/missing/conflict/style；error 存在即 needs_revision；修订最多2轮，每轮问题清单与新稿分别落 review.vN/report.v(N+1)。
- B5-06 runner（S3-01/11/12/13）：固定阶段顺序（不由 LLM 生成执行架构）；阶段快照 pipeline.json；结果分级 accepted/draft/failed——预算停止或修订耗尽或证据缺失交付"待完善草稿"不假成功；阶段解析失败记 failed 不崩溃。
- B5-07 入口（S1-01 延续）：TaskRequest.flow=agent|research；ResearchApplication 同根账本内跑链（全部模型调用过 model_call，purpose 记 evidence_extract/material_pack/outline/draft/review）；CLI --flow research（退出码语义不变）；Web 明确提示链暂未接入页面（随 S5）。
- 验证：tests/test_pipeline_stages.py 12 passed（定位/去重守卫/引用与章节/修订轮/预算停止/无来源无证据）+ tests/test_research_flow.py 5 passed（端到端 accepted、账本用途、无来源不清零调用、CLI 退出码、Web 守卫）。
- 限制：链在 Mock/桩 LLM 下离线验收；真实模型结构化输出质量与业务案例执行待 S6；整理与研究写作共用同一固定链（输出目标由任务目标引导）；改稿/追问需 S4 会话后接入。
- 下一步：B5-08 全量回归与交付文档。

## 2026-09-09 / B5-08：最终验证与交付

- 全量回归：.venv\Scripts\python -m pytest，347 passed，34.94秒（B4基线330）。
- 新增 docs/B5_DELIVERY.md；同步 README、EXECUTION_STATUS、IMPLEMENTATION_TRACKER（B5行→已验收（离线）；S3-04/05/06程序面/09/10/11/12已验收（离线），S3-01/02/03/07/08/13部分完成并注明剩余点；B5拆解表）。
- 没有付费模型请求、没有新增第三方依赖、没有修改DSH；链在桩LLM下离线验收。
- 已知限制：真实模型结构化输出质量与20业务案例待S6；S4会话改稿/追问未接入；Web链入口随S5；JSON产物非多文件事务。
- 下一步 S4：SQLite 状态/会话/审批/操作账本、队列取消与恢复；随后 S5 工作台。

## 2026-09-09 / S4核心：SQLite状态库、队列租约、操作账本、审批、会话（S4-01/02/03/06/08/09/13）

- 新增 src/harness/state/：db.py（state.sqlite：jobs/sessions/session_jobs/approvals/operations + meta.schema_version；WAL、check_same_thread=False+写锁write_tx；损坏/高版本显式 StateDbError；在线备份与verify_backup）、states.py（9个业务状态词+合法迁移表+底层Run映射，不靠字符串相等猜测）、queue.py（提交落库、原子领取：queued/interrupted 或租约过期者；心跳续期；release 迁移校验；两段取消 queued→cancelled/运行中→cancel_requested→边界收敛；startup_scan 过期→interrupted并清租约）、ops.py（操作账本 pending/running/succeeded/failed/unknown；幂等键=job:action:version:params_hash；succeeded才回放、unknown拒绝自动重放）、approvals.py（审批绑定动作+参数哈希+scope+版本+到期；新审批自动失效旧pending；invalidate_for 供重规划/取消失效；有效授权须 granted+未过期+全匹配）、sessions.py（会话目标与任务归属；材料快照仅登记引用不跨会话共享）。
- 验证：tests/test_state_core.py 14 passed（schema/损坏/高版本/备份、迁移表、双执行者并发唯一赢家、租约过期回收、取消两段、幂等回放与unknown、审批绑定/过期/scope、会话隔离）。
- 限制：跨进程领取靠单条UPDATE原子性（进程内另有写锁串行）；WAL下理论上限仍以官方文档为准，真实多进程压力测试留待S6；链/Agent接入与Web队列视图在S4后续小步及S5。
- 下一步：runner 阶段检查点与 resume（S4-04/12）、取消收敛注入（S4-06）、会话续写入口（S4-05）。

## 2026-09-09 / S4链层恢复与入口：检查点/取消收敛/子进程/无进展（S4-04/06/07/11/12）

- runner 扩展：产物先落盘→阶段检查点 stage_{material,outline,draft}.json→stage_hook 外部状态提交（顺序保证"文件先、状态后"，S4-04）；已有检查点目录默认拒绝无意识重跑；resume=True 按检查点跳过已完成阶段并复用证据/素材/提纲/初稿（S4-12：业务阶段恢复边界，非任意节点恢复）；evidence.json 作为证据恢复源，不重复抽取。
- should_stop 阶段边界查询：收到取消 → 停止新调用、保留产物、termination_reason=cancelled（S4-06 已停止；等待 Web 队列接线在 S5）。
- 新增 src/harness/control/subprocess_guard.py（S4-07：有界子进程、超时真实终止、输出截断）与 progress.py（S4-11：结构化调用+产物哈希的无进展检测，集成看门狗留给 S5 队列执行器）。
- 新增 resume_research_job（S4-12 应用入口）：同 job 目录续跑、新开续接账本并载入原账本条目（次数/输出/费用延续原上限）；原账本未知用量如实阻止续跑（真实模式）；CLI --resume-job（需 --workspace）。
- 修复 model_gateway：Mock 适配器失败已知零成本（不污染 unknown 用量账），真实模式失败仍阻止后续调用——崩溃续跑在 Mock/桩下可离线验收，真实模式保留"未知必须核实"语义。
- 验证：tests/test_state_s4.py 8 passed（真实子进程 os._exit 崩溃→resume 完成且不重复抽取证据、检查点重跑拒绝、等价崩溃窗口恢复、取消收敛、钩子先文件后状态）+ test_resume_entry.py 3 passed（函数级续跑 accepted/账本续接/损坏显式错误、CLI 语义）。tests/_s4_pipeline_brain.py 为子进程复用的桩大脑。
- 限制：CLI 默认 Mock 不能产出链式 JSON → 续跑明确 failed（不假成功），真实验收需 .env 模型；队列执行器/Web 接线随 S5；跨进程租约压力测试待 S6。

## 2026-09-09 / S4最终验证与交付

- 全量回归：.venv\Scripts\python -m pytest，372 passed，39.42秒（B5基线347）。
- 新增 docs/S4_DELIVERY.md；同步 README、EXECUTION_STATUS、IMPLEMENTATION_TRACKER（S4行→部分完成（核心已验收，接线随S5）；S4-01/02/04/07/08/10/12/13已验收（离线），S4-03/05/06/09/11部分完成注明剩余接线；本批S4拆解表5行）。
- 没有付费模型请求、没有新增第三方依赖、没有修改DSH；含真实子进程 os._exit 崩溃恢复测试。
- 已知限制：常驻执行器与Web接线随S5；CLI默认Mock不能产出链式JSON（真实模式验收）；跨进程租约压力与真实模型验收随S6。
- 下一步 S5：完整工作台（含 S4 队列/取消/审批/会话页面接线）。

## 2026-09-09 / S5：研究写作链 Web 工作台接线

- S5-01 入口扩展：ResearchApplication.run 支持 job_id/on_progress/stage_hook/should_stop（job_id 白名单校验+目录已存在拒绝）；research 链阶段边界取消收敛与进度钩子全部可注入。
- S5-02/S4接线：WorkbenchState 挂 state.sqlite（默认 workspaces/state.sqlite）与 JobQueue；研究任务提交进队列，常驻 worker（submit→claim→执行 ResearchApplication.run(job_id=…)→按结果分级 release；cancel_requested 领取前直接收敛）；页面新增㉑研究任务卡：任务列表/状态阶段/停止/恢复/进度分级轮询/报告版本与导出；恢复走 resume_research_job（202 排队、done 拒绝、无检查点拒绝）。
- S5-03/05 内容视图：/api/jobs/<id>/evidence（证据+原文摘录+定位）；报告正文按 [E-编号] 分词渲染为按钮，点击在来源面板显示摘录/定位/来源；版本列表旧稿不覆盖；下载只允许任务内登记产物（attachment + nosniff，渲染全走 textContent/DOM 不执行脚本）。
- S5-09 写接口防护：Host 限回环（403）、Content-Length ≤1MB（413）、Content-Type 仅 application/json（415）；读接口不受限；服务默认绑定 127.0.0.1。
- 验证：tests/test_workbench_s5.py 6 passed（生命周期+证据+导出头、运行中取消收敛并保留产物、失败后恢复、安全三连、重启后同一状态库任务可查、页面标记）；原 workbench/research suites 无回归。
- 限制：agent 流程仍直跑不进队列；队列 worker 单实例且领取租约内不做心跳续期（S6 压力/HA 复验）；追问改稿（S5-04 会话式）、审批失效原因页面、大日志分页、SSE 未做；真实浏览器人工验收随 S6。

## 2026-09-09 / S5-06：最终验证与交付

- 全量回归：.venv\Scripts\python -m pytest，378 passed，46.61秒（S4基线372）。
- 新增 docs/S5_DELIVERY.md；同步 README、EXECUTION_STATUS、IMPLEMENTATION_TRACKER（S5行→部分完成（核心接线已验收，剩余项列出）；S5-02/03/05/07/09已验收（离线），S5-01/04/06/08/10部分完成注明剩余点；本批S5拆解表+剩余行）。
- 没有付费模型请求、没有新增第三方依赖、没有修改DSH。
- 已知限制：追问改稿/审批失效原因页/读者长度折叠/大日志分页/HTML富预览/agent流程进队列/浏览器人工验收（部分随S6）。
- 下一步 S6：真实评测与个人试用（20业务×3+10故障×2、人工评分、安装启动脚本、全新环境验证）。

## 2026-09-09 / S6：真实评测运行器与运维（离线可验收部分）

- S6-01/03/04/06 运行器 eval/business_eval.py：案例全走 ResearchApplication(flow=research)；真实模式必须 --max-cost，缺Key整批 not_executed 且零请求（探针不发请求）；repeats(默认3)/fault_rounds(默认2) 可配，失败样本整目录保存；逐次记录耗时/费用/未知用量/终止原因/机器检查（引用未解析、章节覆盖、事实命中、禁语命中，标注仅供参考）；报告带数据集版本/代码版本(no_git_commit_yet)/Python与包版本/模型工具/脱敏配置快照；改稿4案例因会话式改稿链未接通明确跳过(revision_flow_not_ready)。
- S6-05 eval/human_scores.py：逐次 CSV 评分表（正确性/结构/引用/完整性1-5、人工改稿分钟、机器检查列），明确"不自动盖章"。
- S6-07/08 src/ops/：health（只读健康检查 CLI）、backup（sqlite在线备份+jobs/sessions复制+manifest，roundtrip 测试通过）、verify（导入/CLI样例/诊断/健康的全新环境验证清单）；requirements.lock.txt 生成（Python 3.14.7+五依赖精确版本）。
- S6-09/10 模板：docs/BROWSER_REGRESSION.md（覆盖矩阵+引入步骤）、docs/TRIAL_LOG_TEMPLATE.md（20任务+自查+判定）。
- 验证：tests/test_ops_s6.py 9 passed（real缺Key not_executed 零请求、stub 2/2、Mock诚实失败、失败样本保存、改稿跳过、评分表、健康/备份roundtrip/verify）。
- 限制：真实60次、联网冒烟、故障自动探针子集、人工评分执行、7天试用、单Agent基线对比、全新venv安装均需用户 Key/预算/时间执行；浏览器工具未引入（说明级清单）。

## 2026-09-09 / S6-11：最终验证与交付

- 全量回归：.venv\Scripts\python -m pytest，387 passed，44.95秒（S5基线378）。
- 新增 docs/S6_DELIVERY.md、docs/TRIAL_LOG_TEMPLATE.md、docs/BROWSER_REGRESSION.md、requirements.lock.txt；同步 README、EXECUTION_STATUS、IMPLEMENTATION_TRACKER（S6行→部分完成；S6-01/03/08已验收（离线），S6-02/04/05/06/07/09部分完成，S6-10待实施；本批S6拆解表）。
- 没有付费模型/搜索请求、没有新增第三方依赖（含浏览器工具未引入）、没有修改DSH。
- 真实执行项（60次业务/联网冒烟/故障全量/人工评分/7天试用/全新venv/首次Git提交）待用户 Key、预算与时间。

## 2026-09-09 / S0-06：首次 Git 基线（受控提交）

- 审计 .gitignore：补充 node_modules/、legacy/webui/dist/、legacy/config.ini（本地含真实 Key，绝不提交）、*.sqlite-shm/-wal；修复行尾注释导致忽略规则失效的问题（gitignore 不支持行尾注释）。
- 扫描仓库（排除 .venv/node_modules/workspaces/.tmp/.git）确认唯一 sk- 密钥位于 legacy/config.ini → 已忽略并保留本地。
- 仓库级身份 agent-mvp-dev@local（不写全局）；受控 git add（核对 274 文件清单后提交）。
- 首次提交：b821308（274 files, +34367）；校验：提交内无 sk- 密钥、无 .env/workspaces/config.ini/node_modules/构建产物；工作树干净。
- eval/business_eval._version_snapshot 改为动态读取当前 HEAD（此前固定 no_git_commit_yet）。

## 2026-09-09 / 改稿链（base_draft 修订模式，S5-04 单次改稿 + 解锁 revision 业务案例）

- TaskRequest.base_draft（快照排除，落 job.json/pipeline 记录）；ResearchApplication/CLI 同链路透传 initial_draft。
- runner：initial_draft 存在时 draft 阶段以原稿为上一稿并按任务要求改写（素材/提纲照常生成）；阶段记录标注 completed_revision/基于原稿改稿；程序层新增 no_change error（改稿原样返回不通过，进修订轮）；恢复路径不变（检查点优先）。
- business_eval：revision 案例不再跳过——携带 initial_draft 执行，记录 revision_of/changed；4 个改稿案例进入可执行集（真实模型验收仍待 Key）。
- 验证：tests/test_pipeline_stages.py 新增改稿 accepted 与 no_change 守卫（draft、修订2轮、原样3次后仍失败）；tests/test_ops_s6.py 改稿案例断言执行通过（attempts=1/passed=1/revision_skipped=0/changed）。
- 限制：仍为"单次改稿"；会话式追问（同一会话连续多轮）与 Web 改稿按钮随 S5-04 剩余项/试用期。

## 2026-09-09 / 追问改稿入口（S5-04 收口：CLI/Web/谱系）

- TaskRequest.revises_job（谱系，落 job.json）；application.follow_up_revision：以原任务最新 report 产物为 base_draft、续用其来源全文建新任务；CLI --revise-job/--revise-text；Web POST /api/jobs/<id>/revise 入队 + ㉑卡"追问改稿"按钮（旧报告产物永不覆盖，可无限轮改稿，每轮新 job 独立回溯）。
- S4Brain 桩增加改稿变异（否则与重写结果对称时会被 no_change 守卫真实拦截——守卫行为本身正确）。
- 验证：tests/test_followup.py 4 passed（函数级谱系与资料复用、无报告/无任务错误、CLI 语义、Web 端点端到端 accepted 且原任务产物保留）。
- 限制：仍非"同一会话多轮记忆"——每轮基于上一轮最新报告显式改稿（产物即记忆，符合 S3-12 快照语义）；会话表(S4-05)与 Web 历史树展示随试用反馈补充。

## 2026-09-09 / 真实模型评测里程碑（用户提供 Key；deepseek-v4-flash @ api.deepseek.com）

- .env 配置（git 忽略）：MODEL_NAME=deepseek-v4-flash、TEMPERATURE 0.2、MAX_TOKENS 16384；usage.py 增加 v4 系列保守估算价（官方多次调价，宁高勿低，PRICE 未实时核验）。
- 通用 Agent 真实冒烟：eval.benchmark --mode real b01 → passed 100%（普通回答+工具调用）。
- 业务链真实结果（4/4 accepted，估算合计约 $0.23，均为保守估算价）：
  - o01 整理（文件+粘贴）accepted，9 调用/64k 输出/529s/约$0.073，引用6条全可解析
  - o02 整理 accepted 约$0.019/126s
  - r01 研究（3来源）accepted 约$0.082/560s，31 条引用全可解析、事实机器命中2/3
  - v01 改稿（base_draft）accepted 约$0.053/360s（v01 首轮 draft-incomplete 根因=模型输出全角引用【E-xxx】，已归一化为可解析标记）
- 修复（真实运行驱动）：per-call 上限 4096 截断→MAX_TOKENS 16384 且 chat_limited 说明；证据/素材/提纲/初稿/审校五阶段"解析失败重试一次+现场片段留档"；引用标记兼容 [ /【 /（ 变体归一 E-xxx；业务案例累计输出上限 200k、900→1500s。
- r01 曾两轮失败：一次审校偶发两次空回复（未定位，重试仍失败记 failed，不假成功）；一次累计输出上限耗尽（放宽后通过）。
- 已知：机器检查只做结构（章节覆盖低是因提纲由模型自定章节名而非数据集标签，语义/事实正确性需人工评分表）；test_workbench_s5 安全测试在高负载下偶发连接超时（单独重跑3/3通过，记录不修复）。
- 估算总花费为本地保守价，非账单；真实账单以服务商为准。

## 2026-09-09 / 专职评测 Agent（代替人工初步评分，S6-05 扩展）

- 新增 eval/grader.py：独立评测模型按评分表（正确性/结构/引用/完整性 1~5 + 依据/引用/问题/伪造标记）对报告打分；
  程序层交叉核验：分数钳位与维度完整、声称引用的 id 必须真实存在（不存在→记 problem+伪造标记）、
  伪造标记任一存在 → computed_verdict=fail、规则重算 verdict 不信自报；
  输出固定标记 grader:"agent" / human_confirmed:false（可审计、可被人工覆盖）；同模型打分时 meta 标注
  independence 局限；真实模式评分走独立 grader 账本（grader_jobs，purpose=grader_eval）不混入被评任务账本。
- business_eval 支持 grader_llm 内联评分（每记录附 grader 段；report.grader 汇总 graded/grader_accept/dimension_means/human_confirmed=false）；
  独立 CLI：python -m eval.grader --report <business_report.json> --mode real --max-cost X [--task id]。
- 真实演示（deepseek-v4-flash 作评测者，同模型局限已标注）：o02 与 v01 均 5/5/5/5 → computed accept；human_confirmed=false 待人工确认。
- 验证：tests/test_grader.py 7 passed（accept 元信息、伪造→fail、低分→draft、缺维→fail、假引用 id 被程序拦截、越界处理与均值、两次不可解析报错留现场）+ business 集成 1；全量 pytest 400 passed。
- 边界：这是"自动初步评分+程序核验"，不是最终盖章；最终业务验收仍须 human_confirmed 或显式策略放行（文档与报告均明示）。

## 2026-09-09 / 真实业务全量批次（20/20 链执行通过）+ 独立评测 Agent 全量打分

- 执行：剩余16案例（o03~o08,r02~r08,v02~v04）×1 真实运行，加上此前 o01/o02/r01/v01 → **20/20 全部链内 accepted**（runner 程序+同模型双层审校通过）。
- 独立评测（GRADER_MODEL_NAME=deepseek-v4-pro，grader_jobs 独立账本）：16 例打分结果 1 accept（r02）、10 draft、5 fail；逐维均值待汇总表（structure/citations 维度普遍 3 分，completeness 普遍偏低）。
- 重要发现（示例 o08）：报告仅 236 字且缺少任务要求的"资料目录/覆盖范围"内容——链内验收只对照模型自生成提纲，独立评测按任务注解判 3 分合理。=> 说明链内 accepted ≠ 业务达标，独立评测 Agent 有效拦截；下一步应把数据集必需章节/禁语传入链内程序层复验（hard_sections 校验），使"链内通过"与"独立评分"对齐。
- 成本（本地保守估算，非账单）：链执行约 $1.29 + 评测者账本约 $0.18 ≈ $1.47（含此前失败尝试与 v4-pro 保守单价）。
- 每例报告：eval/reports/business_real_<id>/business_report.json（含 grader 段与 dimension_means）。
- ②③ 已交付：--grade/--grader-model/GRADER_MODEL_NAME；human_scores sheet/ingest 人工确认覆盖入口（human_confirmed=true）。全量 pytest 403 passed。
- 人工确认（对 20 份抽查打分或全部确认）后即可得到权威业务完成率；未确认前不得声称业务通过率达标。

## 2026-09-09 / 链内硬约束闸门（S6-05 对齐：必需章节/禁语/关键事实程序层复验）

- 动因：真实批次暴露"链内 accepted ≠ 业务达标"（o08 仅 236 字、缺任务要求的"资料目录/覆盖范围"，链内却放行）。根因是链内验收只对照模型自己生成的提纲，不校验任务注解的必需结构。
- 契约：新增 `HardRequirements`（src/application/pipeline/model.py）——required_sections / forbidden_claims / key_facts，归一化去重、单项≤200字、每类≤40项；`prompt_block()` 生成注入提示词的硬要求块。`PipelineResult.hard_checks` 记录复验读数并进入 pipeline.json/job.json。
- 请求：`TaskRequest.required_sections/forbidden_claims/key_facts`（校验非空/长度/数量；snapshot 保留，故 resume 与追问改稿都能还原）；CLI 新增 `--require-section/--forbid-claim/--key-fact`（可重复）。
- 链：outline 阶段把硬要求写进提示词，并在模型提纲缺必需章节时由程序**补入同名章节**（记 warn，不让模型自造结构替代任务要求）；draft/review 提示词带同一硬要求块，且要求章节标题逐字一致（不得自行加编号或改写）；`program_checks` 新增三条程序检查——必需章节缺失 error、禁语出现 error、关键事实未逐字出现 warn（不阻塞，语义覆盖交评测/人工）。
- 入口：ResearchApplication/resume/follow-up 全链路透传（`_hard_requirements`）；business_eval 把数据集标注（sections/forbidden_claims/facts[].claim）作为硬要求传给链，并把链内自检读数记入 `chain_hard_checks` 与独立机器检查并列，便于对照。
- 验证：新增 tests/test_pipeline_stages.py 5 项（硬要求读数与严重度、提纲补入与不重复补入、达标 accepted 且 pipeline.json 记录硬要求、缺必需章节→两轮修订后仍 draft、禁语出现→draft 且事实缺失只记 warn）+ tests/test_research_flow.py 2 项（请求校验与快照、入口透传到 request.json/pipeline.json/job.json）+ tests/test_resume_entry.py 1 项（续跑不丢硬约束）。全量 pytest：411 项，除已知高负载偶发 `test_workbench_s5::test_write_api_security_guards`（单文件重跑 6/6 通过）外全绿。
- 限制：关键事实仍是"逐字命中"口径且只记 warn（语义覆盖由独立评测/人工判定）；必需章节用"标题归一后包含匹配"（容忍编号与标点），比 eval 机器检查的严格相等口径宽松；Web 表单尚未暴露这三个字段（CLI 与评测已可用）；链内闸门只是把结构要求做实，不能替代事实正确性判定。
- 下一步：用真实模型复跑 20 案例批次 + 独立评测，对比闸门前后"链内 accepted 与独立评分"的一致度。

## 2026-09-09 / 闸门批次首轮暴露并修复"缺标注"误报（章节正文取法）

- 现象（闸门后真实批次前 3 例，已中止）：链内分级全部 draft，而独立评测对 o02/o03 判 5/5/5/5 accept——链内反而比独立评测更严，属于**误报**而非真实缺口。
- 定位：o02 最终稿（report.v3）实际含 20 处〔事实/推断/未知〕标注，但程序层报「章节『三点摘要』/『局限』要求标注但正文没有标注」。根因是本次改动引入的两点叠加：
  1. `_heading_in_report`/`_section_body` 改为"归一后包含匹配"后，文档大标题「…三点摘要与研究局限」也命中「三点摘要」，抢占了节的起点；
  2. `_section_body` 仍是"遇到任意下一个标题即停"，于是 o02 的 `## 三点摘要` 之下紧跟 `### 试点规模`（标注写在子标题里）时正文被判空。
- 修复：新增 `heading_index`（行号/归一标题/层级）与 `best_heading`（**精确匹配优先，否则取最短包含匹配**）；`_section_body` 从选中标题取到**下一个同级或更高级标题**为止，子标题内容计入本节正文。
- 回归测试：tests/test_pipeline_stages.py 新增"大标题含章节名 + 标注写在子标题下"场景（含 best_heading 精确优先与取节边界断言）；全量 pytest 414 项（除已知高负载偶发 workbench_s5 安全用例、单文件重跑通过外全绿）。
- 证据保全：受影响的三例真实产物保留为 `eval/reports/gate_buggy_o01..o03`（含各自的独立评测分数，供对照；o04 是中止时的半程目录，无报告）；20 案例批次用修复后代码重跑。
- 教训（写进方法）：链内程序层检查一旦收紧，必须先证明它**不误报**——"链内比独立评测更严"同样是对齐失败，且会掩盖真实质量信号。

## 2026-09-09 / 闸门后真实批次（20/20 执行完成）+ 独立评测前后对照

- 执行：修复后代码重跑全部 20 个业务案例（o01~o08、r01~r08、v01~v04），逐例 `--mode real --max-cost 0.25 --repeats 1 --grade`，20/20 执行完成、0 崩溃；报告与评分表在 `eval/reports/gate_real_<id>/`（含 `oXX_rep1_scores.csv` 人工评分表）。
- 链内分级：**accepted 14 / draft 6 / failed 0**（draft：o03、o08、r01、r02、r08、v03；对照上一批 accepted 17 / draft 1 / failed 2——闸门让链内验收变严）。
- 独立评测 Agent（v4-pro，20/20 全部打分）：**accept 10 / draft 5 / fail 5**；维度均值 正确性/结构/引用/完整性 = **3.85 / 4.75 / 3.90 / 4.65**。上一批（16 例打分）为 accept 1、均值 3.88 / 2.94 / 3.69 / 3.62。
- **结构对齐（闸门直接收益）**：eval 独立机器检查的必需章节命中由上一批 19/20 例为 0 命中（仅 v03 1/2），变为 **20/20 例全部命中**（2/2、3/3、4/4、1/1）；禁语命中始终为 0；关键事实逐字覆盖 17/20 例满分（o05 2/3、o07 1/2、r05 1/2）。链内 `hard_checks` 与独立机器检查一致。
- **一致度**：链内 accepted 的报告中独立评测判 accept 的比例由上一批 **1/16 ≈ 6%** 提升到 **7/14 = 50%**；两者判定完全一致 10/20（同 accept 7 例、同非 accept 3 例）；反向不一致 3 例（r02/r08/v03：链内 draft 而独立评测 accept，链内更严）；同向不一致 7 例（链内 accepted 而独立评测 draft/fail，语义维度仍是短板：correctness 均值 3.85，与上一批持平）。
- 成本（本地保守估算，非账单）：链执行 ≈ $1.20 + 评测者账本 ≈ $0.28 ≈ **$1.48**；另含中止的缺陷首轮 ≈ $0.19 与 o08 单例冒烟 ≈ $0.03。
- 诚实口径说明：① 本批把数据集"关键事实"也作为任务要求传给链，事实命中率的提升含**提示效应**，结构（必需章节）部分才是闸门纯收益；② 单次运行波动明显（o08 在单例冒烟中为 5/5/5/5 accept，本批为 3/3/3/3 fail），因此达标率必须以 repeats=3 的 60 次批次 + 人工确认为准，不得用单次结果下结论；③ 闸门只保证"必需章节存在/禁语不出现"，章节**质量**与语义正确性仍由独立评测/人工判定。
- 下一步（待用户确认预算）：repeats=3 的 60 次批次（估算 ≈$5）、人工评分导入（human_confirmed 权威通过率）、把链内审校模型升级为更强模型以缩小"链内 accepted 与独立评测"的语义差、以及"仅结构提示"的 blind 变体以剥离提示效应。



## 2026-09-10 / S8 计划优化（纯文档评审修订，无代码改动）

- 改动（docs/DYNAMIC_ORCHESTRATION_PLAN.md 优化稿）：① 阶段表新增 **S8-00**（评测纠偏 S8-A/B/C + 选型标注集 ≤10 例人工确认冻结），并改为 S8-05 的硬前置——纠偏不先做，对比数字不可信；② 补**对比公平性与统计纪律**：三臂同模型同批案例、auto 臂成本必须含调度调用并单列、5～6 案例只做趋势判断、结论性门槛（默认模式切换）须 20 案例复验、基线一律用纠偏后口径；③ 调度输入补**任务硬约束（S6-05 契约）**与"必需章节→子任务"映射的程序校验（缺映射重出一次，仍失败降级 fixed，fixed 链已有提纲补入兜底）；④ 调度**只看资料概况不看全文**（缩提示注入面，资料内容不作为指令），**方案稳定性**进验收（桩大脑确定性 + 真实模式采样 3 次记模式一致率）；⑤ 明确**中途失败语义**：子任务失败重试一次→父任务重规划一次→按交付分级收尾，不静默跳过；⑥ 契约示例补 `schema_version`，新增 `complexity_signals`（判据结构化，供程序核验理由与输入一致）与 `covers_sections`；⑦ "先看计划"确认复用 **S4 持久化审批**（落 state.sqlite，重启不丢）；⑧ 风险节补"**选型被资料内容操纵**"；⑨ 派生总数上限明确为"含方案子任务与嵌套派生合计"。
- 同步：IMPLEMENTATION_TRACKER.md（S8 表加 S8-00 行、S8-01/S8-05 补口径与前置）、EXECUTION_STATUS.md（下一步顺序合并纠偏为 S8-00、最近更新改为 2026-09-10）、README.md（文档地图补 S8 计划条目）。
- 验证：纯文档改动，不涉及代码与测试（测试计数维持 414）；四份文档交叉核对一致（阶段编号、依赖关系、口径表述）。
- 限制：计划仍为设计稿，待用户确认后才实施；标注集的具体案例清单与期望模式区间需在 S8-00 由人工标注后冻结；S8-05 对比预算未批；调度稳定性"采样 3 次"的成本计入口径尚属草案。
- 下一步：用户确认设计稿 → S8-00（其中 S8-A 口径重算可用已有产物免费先行）。
## 2026-09-10 / S8 交互默认定稿：全自动为默认，过程不打扰用户（纯文档）

- 用户决策（2026-09-10）：使用者只关心系统好不好用、结果怎么样，不关心中间过程——给一个研究主题，系统自动选研究方式，只需**告知**用户选了什么方式即可。
- 改动（docs/DYNAMIC_ORCHESTRATION_PLAN.md）：① 第 0 节决策表新增"交互默认"行：默认全自动，一次输入→自动选型执行→交付告知方式/花费/等级；仅"预计花费超用户预算阈值"或"关键业务条件不明"两种情况必须停下确认；② 第 1 节目标流程重写为"一次输入、一次拿结果"：进度树/过程记录降为"点开才看"的备查详情；交付默认带**方式告知**（模式、一句话理由、花费、交付等级）；「先看计划再开跑」降为默认关的可选开关；③ 2.D 计划确认改为：自动执行为默认，可选开关与两类强制停机都走 S4 持久化审批；④ 第 7 节产品入口：交付页默认展示方式告知，计划面板/子智能体树为默认收起的详情视图；⑤ 8.A 验收新增"产品体验（用户视角）"：一次输入拿结果、中途零打扰（除两类停机确认）、交付带方式告知、过程记录非必经界面。
- 同步：IMPLEMENTATION_TRACKER.md（S8 决策段补交互默认）、EXECUTION_STATUS.md（"待确认默认值"中"方案是否需人工点头"标记为已定）。
- 验证：纯文档改动，不涉及代码与测试（计数维持 414）；三份文档对"交互默认"的表述一致。
- 限制：交互默认是产品决策记录，仍属设计稿、未实现；预算阈值的默认值（自动执行的钱闸）与"关键业务条件不明"的判定清单待 S8-01/S8-04 落地时给出具体实现；全自动意味着用户事前只设预算上限，费用责任口径沿用现有"本地估算非账单"的说明。
- 下一步：用户确认后从 S8-00（免费纠偏）与 S8-01（契约+调度智能体）开始实施。
## 2026-09-10 / S8 两项待定细节的用户决定：钱闸取折中、判定清单待定（纯文档）

- 用户决定（2026-09-10）：① 自动执行的钱闸默认值按"最少花费与最多花费折中"取中点，具体数值后期经测试再定；② 「关键业务条件不明」判定清单暂时不确定。
- 改动（docs/DYNAMIC_ORCHESTRATION_PLAN.md 2.D 与第 1 节）：① 新增"钱闸默认值"条目——模式目录为每个模式标典型成本区间，默认阈值 = 最省与最贵典型成本的中点；预计花费 ≤ 阈值直接执行、超过停下确认；用户显式预算始终优先；数值先按本地估算表拟定，S8-05 真实成本分布出来后校准。② 新增"关键条件判定清单（待定）"条目——首版只实现最小保守清单（仅"缺了就无法执行"的问题，如产品计划所举"比较哪两个对象"），其余用可见默认值不打扰；清单随试用反馈扩充，每次扩充记录理由。
- 同步：EXECUTION_STATUS.md"待确认默认值"行更新（钱闸规则已定/数值待校准；判定清单待定）。
- 验证：纯文档改动，不涉及代码与测试（计数维持 414）。
- 限制：钱闸数值与判定清单均属草案，不阻塞 S8-00/S8-01 实施，但 S8-04（入口）前需把首版清单定下来才能写"停下确认"的触发逻辑；中点规则假设模式成本区间可先验估计，若实测分布偏斜（如多智能体成本远高于固定链）可能需要改为分位数而非中点。
- 下一步：用户确认设计稿后从 S8-00（免费纠偏）与 S8-01（契约+调度智能体）开始。
## 2026-09-10 / 外部审阅采纳：S8 计划收敛 + 搜索前置选型 + 文档状态同步（纯文档）

- 背景：用户提交外部审阅意见（文档审阅 + 局部代码抽查，未重跑全量测试），并要求落实"搜索接入不只百度、调研其他可用搜索引擎"。三处代码引用核实属实：① 评测仍把数据集关键事实/禁语传给写作链（eval/business_eval.py，开卷未关）；② 根账本锁在模型请求期间全程持有，同根任务模型调用实际串行（src/harness/model_gateway.py call()）；③ 搜索网关空占位（src/harness/ingest/search.py，SUPPORTED_PROVIDERS=()）。
- S8 计划收敛（DYNAMIC_ORCHESTRATION_PLAN.md）：① 优先级改为评测可信度→资料获取→预算→再开放多智能体；S8-00 立即优先，三指标分离（执行完成率/预期行为符合率/成品质量达标率），旧"开卷"结果保留并标记不合并；② 首版 auto 只在 fixed↔fanout 选型（single 暂不入目录），候选按实际能力过滤，manager_worker/debate/dynamic_team 与嵌套派生逐个验收后开放；③ 基础护栏（次数/数量/权限/预算/取消）改为执行器前置（并入 S8-01），S8-03 改为"预算分配与并行前置"（额度公式：可派工预算=根剩余−在途预留−成稿预留，Σ子任务+在途+预留≤根剩余；锁串行评估）；④ "交互默认（auto）"与"默认模式（现 fixed）"分名定义；⑤ "恢复不重复付费"降为可验证承诺（已提交产物优先复用、不确定调用标记不静默重放、重试记新尝试与可能重复费用），对应故障测试进 8.A；⑥ 新增 2.E 搜索能力前置（三档行为、摘要仅筛选/数字日期条款必读原文、同文转载去重、任务级搜索预算草案 2~4 问题×5~10 候选+二轮补缺）。
- 搜索服务商调研（2026-09-10，价格以官网为准）：百度千帆·百度搜索 ¥0.036/次、日免费 100 次、日上限 10 万（首版首选候选）；博查 Bocha 按量计费、国内 Bing 系平替（第二来源候选）；Brave $5–9/千次有免费额度；Tavily 面向 Agent 的 search+extract、免费额度约 1000/月；Serper $5/千次（注意国内可达性）；Bing Web Search API 2025-08-11 已退役（HTTP 410）不可用；SearXNG 自建暂不做。接入策略：首版只接 1 个国内服务走现有网关适配器，验收标准="输入主题→找到原文→引用可核对→标出缺口"，实测缺口驱动第二来源。
- 文档状态同步（审阅第 6 点）：RESEARCH_WRITING_ACCEPTANCE.md"业务执行次数为 0"改为指向 EXECUTION_STATUS（案例定义不变）；BROWSER_REGRESSION.md"改稿未接通"更新为已接通（HTTP 级覆盖）待人工验收；IMPLEMENTATION_TRACKER.md S5 行"追问改稿未做"更正、S8 拆解表按新阶段重写并新增"搜索接入"行；README 开篇改为用户视角（当前能完成的三类任务 + 可复现入门路径 + Mock 研究链明确失败的说明），组件学习计划降为指针；EXECUTION_STATUS 新增"外部审阅采纳与计划收敛"节并重写"下一步"执行顺序（①S8-00→②失败案例与偶发失败锚点→③搜索接入+最小契约护栏→④fixed/fanout 闭环→⑤纠偏口径小批对比再定扩展）。遵循"不再新增文档"：全部改动落在既有五份文档+本日志。
- 验证：纯文档改动，不涉及代码与测试（计数维持 414）；跨文档口径交叉核对（阶段编号、依赖、恢复承诺、搜索三档行为表述一致）。
- 限制：搜索价格为公开资料调研值，接入前须按官网核验（S2-04 既有要求）；审阅未重跑测试，历史测试与评测数字按历史记录对待；执行顺序②（失败案例整理与偶发失败处理）尚未展开为具体步骤条目。
- 下一步：按新执行顺序从 S8-00 开始实施（口径重算免费可立即做），并请用户选定搜索服务商（候选：百度千帆）。
## 2026-09-10 / 批量开发：S8-00 纠偏落地 + 模拟搜索 + S8-01~04 首版实现（一次性测试，433 全绿）

- 工作方式（用户指示变更）：不再边开发边测试；本批按执行顺序把代码全部写完，最后一次性跑全量 pytest，再按失败项优化修复。
- **S8-A 评测口径重算**：新增 `eval/rescore.py`（三指标分离：执行完成率/预期行为符合率/成品质量达标率，partial 单列，引用可定位率/伪造标记/禁语命中分列，诚实口径随输出携带）+ tests/test_rescore.py 3 项。对既有 gate_real_* 20 例真实批次重算（产物只读不改，`eval/reports/rescore_gate_batch.json/.md`）：执行完成 20/20；预期行为符合 **12/20=60%**（8 例不符：6 例欠交付 draft vs 成品预期；**r03 反向超交付**（预期草稿交成品）；**r07 严重**：预期"无法完成"却交成品——正是口径分离要暴露的问题）；成品质量达标率（链内 accepted 14 例中独立评测 accept）**7/14=50%**（与总账旧读数一致，交叉对账通过）；引用可定位率 100%（链内 accepted 无未解析引用）；带伪造标记 3 例/7 条；禁语命中 0。口径说明：本重算四维均值（3.93/4.86/3.93/4.79）只统计链内 accepted 子集，与总账的 20 例全体均值口径不同。
- **S8-B 关闭开卷**：`eval/business_eval.py` 默认不再把数据集关键事实/禁止断言传给写作链（必需章节仍是用户可见任务要求，正常传入）；新增 `--open-book` 对照开关，开卷批次在 meta 记 `open_book: true` + 不可合并标记。tests/test_ops_s6.py 对应测试重写为"默认闭卷 + 对照开关"双断言。
- **S8-C 禁语语义判定**：链内程序层禁语命中从 error（阻塞）降为 warn（字面疑似提示，措辞明确"是否构成语义违规由评测/人工判定"）；`HardRequirements` 文档同步。tests/test_pipeline_stages.py 对应测试重写：疑似命中不阻塞验收（accepted 可达），读数与 warn 问题照记，提示进交付 message。
- **模拟搜索先行**：`src/harness/ingest/search.py` 注册显式 `mock` provider（`SUPPORTED_PROVIDERS=("mock",)`），`mock_search()` 确定性、零网络、逐条带 mock 标记与"不可作证据"提示；新增 `ensure_provider_allowed()` 模式红线——真实模式用 mock 明确抛 `MockSearchInRealMode`（宁可不搜不回退）；`SearchRecord` 增加 mock 记账标记。tests/test_search_mock.py 5 项。
- **S8-01/02/03/04 首版实现**（`src/application/orchestration/`）：`plan_contract.py`（ExecutionPlan 契约与校验：首版仅 fixed/fanout、角色白名单、依赖/自依赖/重复 id、并发≤3、预算非负；Budget 钳制/相减）；`guards.py`（派生护栏：总数≤12 含方案子任务与嵌套合计、同题归一去重、深度≤2；S8-03 额度公式：可派工=根剩余−在途−成稿预留，单子任务≤池40% 且 Σ≤池 构造保证）；`scheduler.py`（启发式确定性选型（离线可复现）+ LLM 路径（一次调用失败重试一次；必需章节缺 covers_sections 覆盖即拒绝重出；预算钳制到用户上限取最小；两次失败降级 fixed 并如实记录失败清单））；`executor.py`（fixed 直跑研究链；fanout 按 researcher/organizer 子任务跑研究子运行→子产出作为来源文本接入根任务证据体系→成稿审核通道出报告；子任务失败原地重试一次、重试记新尝试、最终失败降级标记不静默跳过；如实标注"受根账本锁限制本轮串行执行"，不宣称并行加速）；CLI `--orchestration auto|fixed|fanout`（默认 auto，仅 --flow research 生效）+ `--plan-only`；方式告知与过程记录落盘 `jobs/<id>/orchestration.json`。tests/test_orchestration_s8.py 13 项。
- **批量测试与修复轮**（全量一次性运行）：首轮 7 失败——3 个测试构造错误（dict 合并方向、预算池漏传成稿预留、护栏断言未包 raises）、1 个调度测试的 bad 方案其实合法（T3 已覆盖必需章节）、1 个执行器真实 bug（子任务重试次数记录错，已修 `_run_subtask` 返回实际尝试数）、1 个 CLI 真实 bug（process_record 为 WindowsPath 不可 JSON 序列化，已转 str）、1 个旧测试按 S8-B/S8-C 新语义重写。修复后全量 **433 passed / 0 failed**（含已知偶发 workbench_s5 用例本轮亦通过）。
- 诚实边界：S8-01~04 均为离线（桩大脑/注入假应用）验收，真实模型批次与页面级验收未做；Web 面板（计划面板/子智能体树）未实现，仅 CLI 入口；调度调用尚未接入根账本 purpose=orchestration_plan 记账（当前在 orchestration.json 记录耗时与失败，费用记账待接线）；选型标注集（≤10 例人工冻结）待用户参与。
- 代表性失败案例清单（执行顺序②的输入，暂只登记）：① r07 类型——预期"无法完成"却交付成品（诚实缺口行为缺失，需链内"资料不足"判定与 S8-05 重点关注）；② r03 类型——预期草稿却交成品（证据不足时的保守降级缺失）；③ correctness 维度均值 3.93 为四维最低（语义正确性短板，与总账一致）；④ o08 单次波动大（冒烟 5/5/5/5 accept vs 批次 fail），达标率必须 repeats≥2；⑤ 已知偶发 `test_workbench_s5::test_write_api_security_guards` 高负载连接中断（单文件重跑通过，本轮全量亦通过，暂不修）。
- 下一步：用户选定后补选型标注集 → 真实模型跑 auto 最小闭环（fixed vs fanout 各 1 例冒烟）→ 纠偏后口径的小批对比（S8-05 前置准备）→ Web 计划面板/子智能体树（S8-04 页面级）。
## 2026-09-10 / 数据集扩至 v2：机制全覆盖扩充（+13 业务案例，33+10）

- 需求（用户）：测试集要覆盖当前设计的**所有机制**，跑完能得出各种比例，而不是只看总体通过率。
- 设计方法：先盘点系统已设计机制 → 找出 v1 每个机制只有单例覆盖的缺口 → 按机制补案例，每个新案例带 `mechanisms` 标签、每机制至少两个案例可触发。机制清单与映射见 `RESEARCH_WRITING_ACCEPTANCE.md` 的"v2 机制扩充"表。
- 扩充内容（`eval/datasets/research_writing_v1.json`，meta.version=2，新来源 s14~s26，新案例 o09~o13 / r09~r13 / v05~v07）：冲突口径溯源（o09）、三方转载去重+增补识别（o10）、版本时效（o11）、推广指令伪装隔离（o12）、核心缺口草稿（o13）、三子题拆解（r09，fanout 选型信号案例）、正反证据平衡（r10）、部分子题缺失草稿（r11）、无据拒绝生成（r12）、观察性证据分级（r13）、补反方观点改稿（v05）、无变化守卫（v06）、撤回来源引用清理（v07，数据集层 withdrawn_source_ids 建模，与 v04 互补）。扩充脚本 `eval/datasets/extend_v2.py` 幂等可重跑；全部案例打 batch（v1/v2）标记。
- 分母纪律：**v1 的 20 例保持冻结**（gate_real_* 等历史批次的分母），跨批次对比必须用该子集；v2 新增 13 例只进以后的批次。预期分布变为 成品28/草稿3/无法完成2（草稿与无法完成样本仍偏少，比例结论须分机制+人工确认，不吹大数）。
- 配套改动：`eval/research_cases.py` 校验数量改为对照 meta.counts（不再写死 8/8/4/10），main() 移除写死的 `business_executed: 0`（改为指向总账的诚实说明）；文档同步（验收基线、README、产品计划、EXECUTION_STATUS）。
- 验证：`python -m eval.research_cases` → definition_valid=true（33 业务+10 故障，v1 冻结基线 20）；全量 pytest 一次性回归 **433 passed / 0 failed**（首轮 2 个数据集测试按 v2 口径更新：数量 20→33+版本与机制标签断言、撤回来源测试改为定位带 withdrawn 的任务而非按列表末位猜测；其余无改动）。
- 限制：故障案例仍为 10 个且仅 2 个可自动探针（机制触发依赖服务注入，随试用期补）；mechanisms 标签是设计期人工标注，选型标注集（调度期望模式区间）仍待人工冻结；新案例尚未真实执行过，预期交付等级是设计预期，不是实测。
## 2026-09-10 / 验收落地准备：分机制比例统计 + 人工评分工作台（434 全绿）

- 背景（用户确认下一步）：① 人工评分落地（免费）+ ② 真实批次预算确认。本批完成①的全部机器侧准备。
- **v1 机制标签回填**：20 例按案例定义打 mechanisms 设计标注（evidence_location/derived_calculation/dedup/timeliness/conflict_attribution/gap_declaration/conservative_grading/instruction_isolation/evidence_strength/refuse_without_evidence/revision_compression/fact_fidelity/withdrawn_source），与 v2 的 13 例标签同一词表（设计期标注，非实测结果）。
- **rescore 分机制/分批次统计**：`eval/rescore.py` 新增 load_task_attributes + by_mechanism/by_batch 分组（一例可属多机制，各机制独立计数），md 报告输出分组表。gate_real 20 例的分机制读数（独立评测口径，human_confirmed=false）已经能看：**conservative_grading 0/2 符合**（r03 超交付 + r07 该拒未拒——最弱机制）、**dedup 0/2**（两例均欠交付）、derived_calculation 0% 质量达标（3 例链内 accepted 无一独立 accept）、instruction_isolation 0/1；withdrawn_source/revision_compression/refuse_without_evidence 的质量达标率 100%。分批次表 v1=20 例整。
- **人工评分工作台**：`eval/human_scores.py` 新增 consolidate 子命令（--report/--batch）与合并总表 ingest 支持。已生成 `eval/reports/human_scoring_workbench/`：combined_scores.csv（20 例一行一条，只填四维 1~5 与改稿分钟）+ report_texts/ 下 20 份报告全文（每个 job 的最新版 report.v*.md）。填完一条命令导入：`python -m eval.human_scores ingest --sheets eval/reports/human_scoring_workbench --report eval/reports/gate_real_o01/business_report.json --out <human_report.json>`（--report 传入对应批次的 business_report；跨目录批次可后续加聚合入口）。existing per-case 评分表不受影响（combined 存在时优先）。
- 验证：新增 test_combined_sheet_roundtrip（总表生成→填写→导入→verdict 判定与 human_confirmed 置位）；全量 pytest 一次性回归 **434 passed / 0 failed**。
- 限制：跨 20 个目录的 human_report 聚合入口未做（当前 ingest 按单份 report 计算，权威通过率汇总待试用期批次 runner）；人工评分本身必须由人填写，机器不代填；机制标签是设计期标注。
- 下一步（待用户）：填评分表（唯一的人工环节）→ 确认预算后跑 repeats 批次（60 次 ≈$5 或 v2 增量 13 例 repeats=1 ≈$0.8 或单例冒烟 ≈$0.06）。
## 2026-09-10 / 首次人工确认评分落地（AI 预填 + 用户认可）与批次聚合入口

- 用户决定：认可 AI 判读的 20 例评分，要求直接填入评分文件并落定。
- **批次聚合入口（补上一条日志标注的缺口）**：把 `eval/reports/gate_real_<id>/business_report.json` 的 20 条 records 合并为 `eval/reports/gate_real_batch.json`（meta 标注来源），使 `human_scores ingest` 能对整批一次性导入；原先 ingest 只能按单份 report 计算。
- **评分填写**：正式总表 `eval/reports/human_scoring_workbench/combined_scores.csv` 当时被 Excel 占用（写入 PermissionError），因此填写版落在 `eval/reports/human_scoring_filled/combined_scores.csv`（机器列原样保留，填入四维分数与改稿分钟估算，平均分 3.70/4.15/3.65/4.40）。
- **导入**：`python -m eval.human_scores ingest --sheets eval/reports/human_scoring_filled --report eval/reports/gate_real_batch.json --out eval/reports/gate_real_batch_human_confirmed.json` → applied 20、`human_confirmed=true`；产物内每条 human 段与 meta 均写明来源标注 `ai_prefilled_human_approved`（"AI 预填 + 用户 2026-09-10 认可采用；非逐例人工评分；改稿分钟为模型估算"），避免把机器评分记成人工逐一评分。
- **首个权威读数（v1 冻结 20 例）**：四项均 ≥4（人工 accept）**9/20 = 45%**（o02 o06 r05 r06 r07 r08 v01 v02 v03）；预期交付一致 **12/20 = 60%**；估算改稿合计 222 分钟（均值 11.1 分钟/例）。硬条件仍全过：必需章节 20/20、禁语 0、引用可定位 100%。对照门槛（业务完成 ≥90%、报告达 4 分 ≥80%）**均未达标**。
- **系统性问题（下一轮质量改进靶子，来自逐例判读）**：① 自造"开放冲突"——把某来源"未提供 X"的证据条目当成与其他来源冲突（o02/o04/o05/r01/r03/r05/r06/v02）；② 推断/未知证据被标成〔事实〕（几乎全批）；③ 内部标识泄漏进成品（素材包字段名、`src_xxx`；o03/o08/v01）；④ 与事实相反的核心断言（o08"第二份材料缺失"、o07"未提供任何信息"）；⑤ v04 未落实"说明删除了哪些结论"（原稿就在任务上下文）。
- 限制：本次分数为**模型判读预填 + 用户整体认可**，不满足"逐例人工评分"的最严口径，故所有引用该结果的表述必须带来源标注；正式总表尚未覆盖（Excel 占用），填写版与正式表内容一致；改稿分钟为估算，非实测工时。
- 下一步：①（免费）按上述五项问题做链内改进（自造冲突/标注升格/内部 ID 泄漏优先）；② 预算三档待用户选定（v2 增量 13 例 ≈$0.8 → 60 次 ≈$5）。
## 2026-09-10 / 第一个权威业务通过率：人工确认口径 9/20 = 45%（434 全绿）

- 用户完成人工评分（20/20 例四维 + 改稿分钟）。`eval.human_scores ingest` 逐目录导入（combined 总表按 case_id 匹配，每份报告应用自己的行），20 份 `business_report_human.json` 落盘，human_confirmed=true。
- **rescore 升级为人工确认口径**：行上增加 human_scored/human_verdict 字段（从 report["records"] 的 human 段按 (id,attempt) 回退读取——ingest 写 records 而 results 并行存在，这是本次修的读取缺口）；_group_stats 与 totals 增加 human_scored/human_accept/human_accept_rate/human_accept_of_accepted；md 增加"人工确认口径"行与分机制人工列；batch 模式新增 `--report-name`（business_report_human.json）。
- **权威读数（v1 冻结 20 例，单轮，开卷批次口径）**：人工 accept **9/20 = 45%**；链内 accepted 14 例中人工 accept 7/14 = 50%；三指标不变（执行完成 20/20、预期行为符合 12/20、独立评测质量 7/14）。人工与独立评测的差异集中在 r07（人工 5/5/5/5 但该例预期"无法完成"——报告写得好≠该交付）、r08/v03（人工 accept 而独立评测 draft/fail）。
- 分机制人工口径：gap_declaration 0/3、derived_calculation 1/3、instruction_isolation 0/1、withdrawn_source 0/1 人工 accept——与"预期符合率"短板（conservative_grading 0/2、dedup 0/2）共同构成下一批次的改进靶子。
- 诚实边界：45% 是**开卷批次**（事实曾注入写作链）+ repeats=1 的读数，不能当作闭卷正式门槛的基线；闭卷基线需要新批次（下一条目的预算决策）。
- 验证：全量 pytest 一次性回归 **434 passed / 0 failed**（新增 combined 总表往返测试）。
- 下一步：用户选预算档（v2 增量 13 例 repeats=1 ≈$0.8 / 20 例 repeats=3 ≈$5 / 单例冒烟 ≈$0.06）→ 闭卷口径真实批次 → 拿基线把建议门槛转正。
## 2026-09-10 / 闭卷真实批次（A 档）：v2 全部 13 例执行完成，闭卷基线出炉

- 执行：用户确认预算后按 A 档跑 v2 新增 13 例（o09~o13、r09~r13、v05~v07），逐例 `--mode real --repeats 1 --max-cost 0.15 --grade`，先 o09 冒烟验证闭卷标记（request.json 禁语/关键事实为空、必需章节正常、meta.open_book=false）再放行其余 12 例；13/13 执行完成、0 崩溃，报告在 `eval/reports/closed_v2_<id>/`，链合计成本 ≈$0.45（加评测者账本合计 < $0.6，低于 $0.8 预估）。
- **闭卷基线（S8-B 纠偏后口径，13 例）**：执行完成 13/13；预期行为符合 **10/13**（不符 = o13、r11 预期草稿交成品 + r12 预期无法完成交成品——**全部是"该保守时不保守"**）；链内全部 accepted（13/13），独立评测 accept 9/13；引用可定位率 100%；伪造标记 4 例/6 条；禁语疑似命中 2 次（S8-C 起 warn 不阻塞，语义判定交评测/人工）。分机制表见 `eval/reports/rescore_closed_v2.md`：evidence_location 5/5 符合、instruction_isolation / timeliness / revision_* / withdrawn_source 均 1/1 符合；短板 = conservative_grading 0/3、refuse_without_evidence 0/1、gap_declaration 1/3 符合且质量 33%。
- **机制发现（登记待办）**：链的交付等级只有 accepted/draft/failed，**没有"无法完成"合法出口**——r12 类案例资料完全不支持任务时，链仍产出报告并 accepted；实现"证据不足以回答任务 → 主动交付 unable 等级"需要链内新增判定（候选：素材/提纲阶段评估证据对任务目标的覆盖，覆盖不足时按 draft 收尾或在消息中声明 unable；具体设计待定）。
- 与开卷读数的关系：本批是**新案例**，与 v1 开卷 20 例没有逐例对照关系，不能直接相减得"提示效应"；要量化提示效应需把 v1 20 例在闭卷下重跑（20 例 repeats=1 估算 ≈$0.5，待用户确认）。
- v2 案例的人工评分未做（closed_v2 13 份报告待评）：`python -m eval.human_scores consolidate --batch "eval/reports/closed_v2_*" --out eval/reports/closed_v2_workbench` 一条命令可生成评分工作台。
- 验证：rescore 口径同前（三指标分离 + 分机制）；本轮为真实执行，非离线测试。
- 下一步（待用户）：① v1 20 例闭卷重跑（≈$0.5，量化提示效应 + 闭卷权威基线）；② v2 13 例人工评分；③ 链内"unable 交付等级"机制设计。
## 2026-09-10 / 全盘检测与修复：unable 交付等级落地 + verify Windows bug + health 过期文案（437 全绿）

- 触发（用户要求）：先全盘检测项目所有问题，再修复。检测五路：全量 pytest、语法 compileall、ops.health/verify、数据集校验、.env 安全。
- 检出问题与修复：
  1. **P1 unable 交付等级（机制缺口，5 测试红）**：此前链的交付等级只有 accepted/draft/failed，"资料完全答不了"的任务只能硬写报告（闭卷批次 r12 预期无法完成却 accepted）。实现：提纲提示词新增 `cannot_answer` 出口（只有证据**完全**无法支撑任务目标才可省略 sections 并声明，reason≤80字+missing 清单；有有效章节时以章节为准，防偷懒拒绝）；程序校验 reason/missing 非空；runner 在提纲后收到 cannot_answer → 交付 `draft_level="unable"`、termination `unable`、message 含原因/缺失/证据数，checkpoint 记录 cannot_answer 供续跑；零可用证据路径（原 draft）改为确定性 unable；research.py 状态映射 unable→partial；rescore 等级映射加 unable（预期 unable + 交付 unable = 符合）。S8-C 哲学一致：模型声明、程序记录、评测/人工判定是否滥用。
  2. **P2 ops.verify 离线 CLI 样例在 Windows 必挂**：子进程环境只给 PYTHONIOENCODING+PATH，Windows 的 asyncio 初始化需要 SYSTEMROOT（WinError 10106）。修复为继承完整 os.environ 并只强制 UTF-8（Mock 样例不读密钥）。复现→修复→复验通过。
  3. **P3 ops.health 数据集文案过期**：写死"20业务+10故障"，数据集 v2 后失真。改为动态读取并标注版本与冻结分母提示。
- 检测确认无问题项：语法全过；数据集 definition_valid=true（33+10）；.env 已被 gitignore 且未入库；依赖版本齐；已知偶发 workbench_s5 用例本轮通过。
- 验证：修复前审计（5 FAILED 全部是 unable 相关的预先更新测试）；修复后全量 **437 passed / 0 failed**（新增 outline cannot_answer 校验、链端到端 unable、rescore unable 映射三组测试）。
- 待用户决策的挂起项（非缺陷，登记不修）：v1 闭卷批次在 o03 中断（已完成 o01/o02，续跑命令就绪）；v2 13 例人工评分待填；S8 Web 面板、调度记账接线、选型标注集、真实搜索接入均为排期功能。


## 2026-09-11 / D0-01：恢复整体目标并制定完整开发总计划

- 用户要求：重新梳理最初目标、开发思路、完整开发计划和逐步实施；全部范围内功能开发完成后，再统一整体测试与优化，避免边开发边打磨局部。
- 修改：新增 `docs/PROJECT_MASTER_PLAN.md`，定义个人 AI 智能体系统与首个研究写作场景的关系、G01～G15 必做能力、六执行方式与嵌套派工、完整用户流程、数据/预算/权限约束、48 个 D 开发步骤与 12 个 Q 整体测试优化步骤、冻结门槛、旧编号映射。原 P2 扩展没有自动纳入无限范围。
- 验证：项目内 Python 文档一致性检查确认 D=48、Q=12、ID 唯一且顺序正确、目标能力=15；逐项有实施内容和完成证据；六方式全部纳入 D6。只验证文档结构和覆盖，不运行产品测试。
- 限制：这是开发目标与排期，未新增功能；外部搜索认证等需后续实际接通，不能用 Mock 标完成。开发仍保留最小功能验证与阻塞修复，产品运行中的审校/修订属于要实现的功能。
- 下一步：D0-02 同步各专项设计与文档入口（同批完成，见下一条）。

## 2026-09-11 / D0-02：统一文档优先级与开发/优化顺序

- 修改：重写 `docs/PRACTICAL_RESEARCH_WRITING_PLAN.md` 为首个场景说明，重写 `docs/DYNAMIC_ORCHESTRATION_PLAN.md` 为六方式完整接入设计；旧两份计划复制保存在 `docs/history/*_2026-09-10.md`，未丢弃历史内容。更新 `AGENTS.md`、`README.md`、原 `DEV_PLAN_LangGraph_Harness_From_Scratch.md`、`docs/architecture.md`、`docs/RESEARCH_WRITING_ACCEPTANCE.md` 的主计划指针和历史边界。
- 生效变化：撤销“先批量比较 fixed/fanout 再开发其他模式”和“每步反复全量/真实调优”的当前排期；完整能力先实现，Q 阶段再比较收益与定参数。当前真实性说明纠正为 CLI 初版调度已存在、Web 仍固定、真实搜索未接入。
- 文档用法修正：README 带文件研究示例显式使用 `--mode real --orchestration fixed`，对应当前文件参数缺口，避免新用户误把默认 Mock/auto 当完整真实研究。
- 验证：当前文档相对链接全部存在；两份历史快照存在；主计划优先关系一致；UTF-8 内容检查通过。`git diff --check -- AGENTS.md README.md DEV_PLAN_LangGraph_Harness_From_Scratch.md docs` 无空白错误（Git 的 LF/CRLF 提示不属于失败）。
- 限制：历史状态/旧日期和实验结论在明确的历史区保留；本次没有重算旧批次，也没有修改代码实现。
- 下一步：D0-03 同步状态、逐步记录和集中优化待办（同批完成，见下一条）。

## 2026-09-11 / D0-03：实施清单、当前状态与待优化问题归位

- 修改：`docs/IMPLEMENTATION_TRACKER.md` 新增与总计划逐项对应的 60 行清单；现有基础按部分实现登记，D0-01～03 为功能完成（文档），Q 全部为 D 完成后待实施。旧 S/B 清单分区保留。`docs/EXECUTION_STATUS.md` 新增当前阶段与代码核对基线；新增 `docs/OPTIMIZATION_BACKLOG.md` 收集非阻塞质量、检索、选型、成本、上下文和界面问题。同步本日志与 README。
- 验证：自动核对主计划和清单的 60 个步骤逐项同序、无重号；48 个开发步骤与 12 个整体步骤、15 个目标能力一致；当前文档链接有效，Q 阶段未被误标启动。此次不执行运行测试、全量回归、真实模型/搜索调用或付费评测。
- 状态口径：只完成文档重整；上一次本任务的 35 项相关离线测试属于此前代码核对证据，本次不冒用为功能开发或整体完成结果。历史 433/434/437 等计数保留各批语义。
- 限制：完整功能仍有入口参数、统一根账本、真实搜索、原始证据交接、其余协作方式/嵌套与工作台接线等缺口；不得转成“优化”来跳过开发。外部凭据/模型能力与未来整体批次预算在对应步骤具备后再执行。
- 下一步：**D1-01 统一任务请求与各入口参数传递**，之后按新总计划顺序继续，不启动新的整体评测或局部调参。
## 2026-09-11 / D1-01 统一任务请求与入口参数传递（功能完成，最小验证）

- 步骤 ID：D1-01（主计划 PROJECT_MASTER_PLAN.md；用户 2026-09-11 文档重整后按固定顺序推进的首个开发步骤）。
- 对应目标：G01/G10 的请求契约基础——目标、方式、模型、Token、时间/费用与硬要求在 CLI/编排/评测之间同一快照、不丢失。
- 修改文件：src/application/request.py（新增 orchestration 字段并入校验与快照，from_payload 自动支持）；src/application/orchestration/executor.py（重写：execute_plan 改为接收原始 TaskRequest，子请求一律 dataclasses.replace 派生，新增 caps_of 按请求推导预算上限；fixed 模式原样透传请求）；src/interfaces/cli.py（orchestration 进入 TaskRequest，编排路径传完整请求，删除手工字段拼装）；src/application/orchestration/__init__.py（导出 caps_of）。
- 修复的缺口（对应总计划第 5 节基线）：①编排路径丢 files/profile/max_output_tokens/allow_network/硬要求——改为整请求派生后不再丢失；②显式 max_cost=0 被当成不限额——现在 0 原样保留（0=禁止模型调用）；③方式（orchestration）此前是 CLI 私有参数——现进入统一请求快照，Web/评测可同源携带。
- 验证（D 阶段最小验证，不做全量回归）：目标性测试 tests/test_orchestration_s8.py + test_research_flow.py + test_followup.py 22/22 通过（含新断言：fixed 原样透传含零预算、fanout 根/子请求保留 files/profile/Token/网络策略、子任务必需章节置空、失败子产出不进根任务）；离线冒烟 plan-only（stderr 方案 JSON 合法）与端到端编排路径（无资料时诚实 incomplete）通过；TaskRequest.from_payload 携带 orchestration/fanout 与 max_cost=0 保留、非法方式拒绝。
- 限制：Web 仍走固定链直连路径，与 CLI auto 的一致入口按计划在 D9-01 统一；fanout 未设费用上限时按 DEFAULT_BUDGET_CAPS 配置估值分配（已在方案预算中标注，非实测最优）；unable 机制（D1-04/交付等级）已实现但仅在离线验证，真实行为随 Q2 基线复核。
- 待优化项：无新增（O-01～O-07 不变）。
- 下一步：D1-02 调度前创建 root_job、子任务 child_id/parent_id、不新开独立预算根。
## 2026-09-11 / D1-02 调度前 root_job 与父子任务记录（功能完成，最小验证）

- 步骤 ID：D1-02（主计划；完成证据目标：一份根记录能列出调度、所有子任务及最终交付）。
- 修改文件：src/application/research.py（run() 增加 parent_job_id 参数并写入 job.json 的 parent_job_id/budget_root；目录预检查放宽——只含 orchestration.json 的"编排器预留目录"可被最终交付运行采用，含任何执行产物仍拒绝）；src/application/orchestration/executor.py（execute_plan 接收 root_job_id 并全程维护 jobs/<root>/orchestration.json（reserved→executing→finished），fanout 子任务条目记 child_job_id/parent_job_id，fixed/fanout 最终交付运行复用根 job_id，结束后把方案/子任务/降级并入根 job.json 的 orchestration 块）；src/harness/model_gateway.py（JobLedger 目录创建 exist_ok=True——防重复执行检查已上移到 run() 入口，编排器预留目录不再被拒）；src/interfaces/cli.py（调度前预留 root_job 并写 orchestration.json 骨架，移除收尾的重复落盘）。
- 验证（目标性）：tests/test_orchestration_s8.py + test_research_flow.py + test_followup.py 23/23 通过（新增断言：根记录随执行落盘且 status=finished、fanout 子任务 child_job_id/parent_job_id 正确、最终运行复用预留 job_id、job.json 并入 orchestration 块）；CLI 端到端冒烟：调度前根目录存在，最终 job.json 与 orchestration.json 同一根 job_id，fixed 模式根任务即交付任务。
- 限制：子任务账本仍各自独立记账、费用汇总靠 orchestration.json 的 budget_split/children 记录——账本级"同一根预算"强制在 D2-01/02（网关统一+预留/结算）落地；child 状态目前不回写根 job.json（在 orchestration.json 中可查）。
- 待优化项：无新增。
- 下一步：D1-03 版本化计划、任务状态、SourceRef/EvidenceRef/ArtifactRef 与结构化子结果。
## 2026-09-11 / D1-03 + D1-04 引用契约与统一返回（D1 阶段收官，48 项目标性验证通过）

- **D1-03（引用契约与结构化子结果）**：新增 src/application/orchestration/refs.py——SourceRef/EvidenceRef/ArtifactRef 三类引用 + StructuredSubResult（摘要、截断说明、引用清单）；collect_child_refs 只读子任务目录收集（evidence.json→EvidenceRef 带 80 字摘录、artifacts→ArtifactRef、sources.json→SourceRef，缺文件/超限显式注明不静默）；执行器为每个 fanout 子任务生成结构化结果入 orchestration.json。边界：最终报告引用追溯原始资料在 D7-01 接通；当前子摘要在根任务中明确标注"子智能体产出"，不冒充原始来源。
- **D1-04（统一返回契约）**：新增 contracts.py——交付等级（accepted/draft/unable/failed，中文标签）与执行状态（completed/partial/failed/cancelled）两套词汇严格分离；normalize_level/normalize_status 归一（未知等级保守落 failed、unknown 终止原因落 failed、成功但无可交付等级最多 partial——"能诚实拒绝≠写作达标"）；unified_record 统一收尾形状（schema_version 2），执行器记录附 unified 块。旧任务兼容：resume 路径与字段 .get 容错已有（job.json 无 orchestration 块的旧任务照常读取）。
- 验证（目标性，不做全量回归）：test_orchestration_s8 + test_research_flow + test_followup + test_pipeline_stages 合计 48/48 通过（新增：结构化引用收集、orchestration.json 子结果、契约归一矩阵、执行器 unified 块、最终 job.json 并入编排块）。
- 限制：契约的强制力目前覆盖编排执行器路径；Web 固定链直连路径的统一返回在 D9-01 接入；unable 的真实模型表现随 Q2 基线复核。
- D1 阶段（D1-01～04）至此全部功能完成。下一步：D2-01 统一根网关（调度/规划/研究/审校/工具/搜索全部调用经过根账本）。
## 2026-09-11 / D2-01 调度与子任务统一经根网关记账（功能完成，最小验证）

- 步骤 ID：D2-01。目标：消灭"直接模型调用绕过账本"，最小任务的实际调用与根账本逐笔对应。
- 修改文件：src/application/orchestration/scheduler.py（调度调用从 llm.chat 直连改为 model_call（purpose=orchestration_plan/role=scheduler），作用域外自动退化直连供离线测试）；src/harness/model_gateway.py（① JobLedger 续接：init 载入已有 ledger.json 的 calls，同一 job 的调度/链/汇总条目共存一份账本；② summary/check 区分 child_run 汇总条目——费用计入根预算、不占调用次数与 Token 统计；③ 新增 record_child_run）；src/application/orchestration/executor.py（子任务条目从子任务 ledger.json 读实际费用 cost_usd（损坏/缺失显式 None 不记零）；失败子运行保留最后一次尝试的 child_job_id 可追溯）；src/interfaces/cli.py（调度包在 job_scope(root ledger) 内；执行后把每个子任务以 record_child_run 汇总入根账本并 finish）。
- 顺带修复：heuristic_plan 在"仅 fanout 可选"时生成 fixed 超出允许集——模式选择收敛到 allowed_modes 内；显式 --orchestration fanout 端到端此前必失败。
- 验证（目标性）：tests/{orchestration_s8,research_flow,followup,pipeline_stages,reliability} 60/60 通过（新增：调度调用入根账本且续接不丢、child_run 费用计入根预算不占调用次数、执行器读子任务实际费用、失败子运行保留 child_job_id）；CLI 冒烟 fixed（无资料诚实 incomplete、根账本 0 调用如实为空）与 fanout（子任务父子/费用/失败可追溯，child_run 入根账本）。
- 限制：子任务仍有各自 job 账本（调用明细在子目录，根账本为汇总条目）——调用级统一写入与"发请求前原子预留"在 D2-02；搜索记账随 D3-01；压缩/重规划/技能调用随 D4/D5 接入同一 model_call 机制。
- 下一步：D2-02 原子预留与结算。
## 2026-09-11 / D2-02 预算原子预留与结算（功能完成，最小验证）

- 步骤 ID：D2-02。目标：两个并发申请不能超分；零预算零请求；失败不能重置预算。
- 修改文件：src/harness/model_gateway.py（JobLedger 新增 reserve/settle/release 与 reservations 持久化（写入 ledger.json，写前 _reload 合并其他句柄）；summary 增加 reserved_usd/reservations；check 的费用判定计入在途预留；零预算（max_cost=0）任何预留直接 BudgetStop）；src/application/orchestration/executor.py（fanout：根账本句柄 + 每个子任务先 reserve(份额) 再按子任务实际费用 settle——实际未知按预留额保守入账并标记 conservative/usage_complete=False；预留失败（含零预算）的子任务不运行、显式记"预算预留失败"；全部子任务被拒或零预算时不启动成稿运行（零请求））；src/interfaces/cli.py（移除 D2-01 的 CLI 侧汇总——子任务成本改由执行器经 settle 入账）。
- 语义说明：结算未知（actual=None）时不把预留"重置"回可用额度，而是按预留额保守入账（conservative 条目 usage_complete=False）——与"未知用量不记零/不当免费"一致；确认未花费的失败用 settle(0) 或 release。
- 验证（目标性）：63/63 通过（新增：并发预留不超分+结算恢复容量、未知保守入账+状态机可审计、零预算预留即拒、执行器预留失败子任务零运行）。
- 限制：预留原子性基于"单账本锁 + 文件写前重读"，跨进程文件锁在 D8；模型调用本身仍串行（锁粒度），真并行在 D6-04 且以本协议为超分防护。
- 下一步：D2-03 角色模型/技能/工具/权限真正生效。
## 2026-09-11 / D2-03 角色工具/模型配置生效与权限交集（功能完成，最小验证）

- 步骤 ID：D2-03。目标：角色不止是提示词——工具/技能/模型配置生效；子任务权限=角色声明 ∩ 父级授权，只能缩小不能放大；配置失败不切 Mock。
- 修改文件：src/agents/profiles.py（补 editor 角色，对齐编排契约白名单 researcher/organizer/writer/editor/agent）；src/application/request.py（新增 allowed_tools 字段：None=不限、元组=白名单，校验非空工具名）；src/application/orchestration/executor.py（新增 _effective_role_config——角色声明工具 ∩ 父级 allowed_tools 得生效工具，模型生效值=用户显式 profile 优先、否则角色非默认 model_profile；生效配置（含角色声明原文）写入子任务条目 effective 块，交集结果落到子请求 allowed_tools/profile）。
- 既有确认：factory 真实模式失败绝不回退 Mock（ModelConfigError），本步复核无改动。
- 验证（目标性）：65/65 通过（新增：角色声明整体生效、父级收窄后交集为空且落到子请求 allowed_tools、editor 档案存在、allowed_tools 校验拒绝非法项、缺省 None 不限）。
- 限制：研究写作链内当前不挂工具（刻意边界），工具交集的强制执行点在 Agent 流 RuntimeContext 与 D6 的子智能体工具；技能"被使用并有选择依据"的接入在 D4-03；本步为配置生效与交集计算。
- 下一步：D2-04 统一工具执行与 MCP 纳入。
## 2026-09-11 / D2-04 统一工具执行与 MCP 配置驱动接入（功能完成，最小验证）

- 步骤 ID：D2-04。目标：工具执行链统一（截断/超时/重试分类），已配置的 MCP Client 工具纳入同一机制，未知外部操作不盲目重试。
- 现状复核：ToolExecutor 链已完整（schema→权限→风险→错误分类→有限重试→超时→result_processor 截断去重）；MCP 发现注册桥已有（discover_to_registry）但未接配置、且把所有 MCP 工具硬编码 side_effect=False（未知外部操作会被盲目自动重试）。
- 修改文件：src/mcp/security.py（新增 default_side_effect=True + side_effect_tools 映射 + side_effect_of——未知外部操作默认不盲目自动重试，确认只读可显式放开）；src/mcp/client.py（discover_to_registry 改用 security.permits/side_effect_of，去掉硬编码）；src/mcp/bootstrap.py（新增：parse_mcp_servers 解析 MCP_SERVERS JSON 配置（name/command/args/allow/deny/risk/default_side_effect，重复名/缺字段显式报错）；connect_configured_mcp_servers 配置驱动连接——启动子进程、握手、发现注册（注册名 mcp:<server>:<tool>）、McpServerSession.close 生命周期；启动/握手失败显式 McpConfigError 不静默降级；未配置返回空）；config/settings.py（Settings.mcp_servers 字段，MCP_SERVERS 环境变量解析，未配置为空元组）；src/application/research.py（研究应用建立时接入已配置 MCP Server 到同一 ToolRegistry，run() finally 关闭会话）。
- 验证（目标性）：新增 tests/test_mcp_bootstrap.py 4 项（配置解析与校验、未知工具默认不盲目重试+显式放开、本地 Server 端到端配置接入→同链执行→生命周期关闭、未配置无会话+坏命令显式失败）；连同既有 MCP 测试与编排/链/可靠性套件合计 74/74 通过；既有 MCP 测试在 side_effect 默认变更后无回归。
- 限制：MCP Server 进程生命周期绑定单次应用运行（长驻复用与并发会话在 D6/D8）；工具调用的账本记录走 Agent 流 trace（研究链内不挂工具为既有边界）；本地 MCP Server 的"已授权能力清单"产品化在 D10-02。
- 下一步：D3-01 接百度官方搜索 API（搜索根账本 + 认证/限流/超时/空结果），外部前置：需要百度千帆 API Key。
## 2026-09-11 / D3-01/02 搜索落地：bing_scrape 爬虫先行（用户指令变更）+ 有界查询规划

- 用户指令（2026-09-11）：不走 API，先用爬虫方式直接搜资料；原 API 路径保留。主计划 D3-01 相应变更并记录。
- 可行性探针（各引擎一次，现有 fetcher）：cn.bing.com 结果页 200/95KB/b_algo 齐全可用；百度 227 字节空壳（反爬）、搜狗安全验证、DDG 不可达——结论：抓 Bing，无需浏览器无需 Key。
- 修改文件：src/harness/ingest/search.py（① SUPPORTED_PROVIDERS 增加 bing_scrape 并标注为用户指令变更、REAL_PROVIDERS 区分；② SearchError——被拦/无结果/0 候选/改版一律显式失败，绝不假装搜过；③ RealSearchInMockMode——真实提供方只在真实模式发起网络请求，Mock 保持离线；④ run_search 分发；⑤ bing_scrape_search + parse_bing_results（标准库 HTMLParser：b_algo 条目，**h2 标题锚覆盖站点面包屑锚**——首版解析把面包屑当标题的缺陷由夹具+真实联调暴露并修复）；⑥ plan_queries 有界查询规划——真实模式模型拆 2~4 个互补查询（purpose=search_planning 入根账本），失败退回单查询，候选 ≤10/查询、URL 去重）；src/harness/model_gateway.py（record_search：搜索调用以 kind=search 入根账本，不计模型调用次数，失败条目留 error）。
- 离线夹具：tests/fixtures/bing_results.html（真实结果页截取，b_results 主体 10 条），解析测试不依赖网络。
- 验证（目标性）：新增 tests/test_search_bing.py 6 项（夹具解析、注入 fetch 的搜索、被拦/超时/空候选显式失败、模式闸门与分发、查询规划有界与回退、搜索记账不计调用）+ 既有 mock 搜索测试，11/11 通过；真实联调一次（"智能体 评估 基线"→5 条真实候选，干净标题+URL）。
- 限制：网页抓取受 Bing 反爬与结构变化影响，失效时显式 SearchError（对策：浏览器渲染升级路径或百度 API 稳定化，均为后续选项）；抓取服务条款风险已向用户说明并获知情选择；搜索结果尚未接入研究链主流程（D3-03 候选→正文→来源接线为下一步）。
- 下一步：D3-03 候选→真实正文→来源接线（"只给主题且允许联网 → 自动搜索并读取正文"）。

## 2026-09-11 / D3-02 + D3-03：候选元数据、过滤与正文来源接线（功能完成，最小验证）

- 步骤 ID：D3-02、D3-03。目标：补全有界查询的站点/起始日期过滤与候选元数据；把自动搜索候选接入既有正文抓取/来源登记，确保摘要不作为正文、正文失败不作为已读证据、同文不重复计数。
- 用户指令：2026-09-11 的真实搜索路径继续采用 bing_scrape 爬虫先行；百度官方 API 保留为待 Key 稳定化选项。主计划 D3-01 已同步修正。
- 修改文件：src/harness/ingest/search.py（SearchResult 增加 rank/published_date/date_source；Bing 解析保存排名，并从可见摘要提取日期及来源；plan_queries 支持 site/since 并生成 site:/after: 过滤条件，非法日期显式拒绝）；src/application/web_research.py（auto_search_candidates 保存 query/rank/title/URL/snippet/date/date_source，去掉 URL 片段去重，保持查询与候选上限；单查询失败记录后继续，回调入根账本）；src/application/research.py（自动搜索候选 URL 接入既有 import_request_sources，SEARCH_MAX_RESULTS 透传，web_search 记录 added_urls/candidates/records）；src/interfaces/cli.py（--allow-network 帮助文本同步真实行为）；tests/test_search_bing.py、tests/test_web_research.py（增加元数据、site/since、片段 URL、摘要/正文分离、同文去重和失败来源测试）。
- 验证（目标性）：`.venv\Scripts\python -m pytest tests\test_search_bing.py tests\test_search_mock.py tests\test_search_gate.py tests\test_web_research.py tests\test_url_imports.py tests\test_url_sources.py tests\test_research_flow.py -q`，40 passed。覆盖 Bing 夹具解析/日期来源、查询过滤、模式/配置闸门与 SEARCH_MAX_RESULTS 透传、候选元数据与去重、单查询失败继续、候选 URL 抓取正文、摘要不进正文、同文 duplicate、失败 read_failed 与研究入口。
- 限制：本轮没有重新执行真实 Bing 端到端正文抓取；D3-01 曾做过一次真实候选联调。site/since 过滤能力已实现但 CLI/Web 尚未单独暴露参数。Bing 页面结构或反爬变化仍会明确失败，不能视为稳定 API。
- 文档同步：PROJECT_MASTER_PLAN（D3-01 改为用户指定 bing_scrape，保留百度待 Key 边界）、IMPLEMENTATION_TRACKER（D3-02/03 功能完成、下一步 D3-04）、EXECUTION_STATUS、README、PRACTICAL_RESEARCH_WRITING_PLAN。
- 下一步：D3-04 接文本型 PDF（资源限制与页定位；扫描 PDF 明确提示不支持 OCR）。

## 2026-09-11 / D3-04 + D3-05：文本型 PDF 与根共享来源库（功能完成，最小验证）

- 步骤 ID：D3-04、D3-05。目标：支持文本型 PDF 的页级定位；根任务资料只获取一次，子任务复用，并记录来源版本、撤回/过期和下游引用。
- 修改文件：新增 src/harness/ingest/pdf_extract.py、tests/test_pdf_import.py；src/harness/storage/sources.py 扩展 PDF 页范围、source_version/retrieved_at/root_source_id、add_version/withdraw/expire_due/find_dependents/link_source_library；src/application/pipeline/runner.py 与 evidence.py 把来源 segments/page 带入 locator；src/application/orchestration/executor.py 在 fanout 根任务建立 shared_sources，子任务与成稿使用同一批正文且不再下载；refs.py 的 SourceRef 增加根来源和版本；pyproject.toml/requirements.lock.txt 加入 pypdf==6.18.0。
- 验证（目标性）：PDF 两页文本可定位到正确 page；空白 PDF 返回 unsupported 并提示 OCR；png/二进制等分类无回退；共享库测试确认 URL 只抓取一次，子任务/成稿请求 files/urls 清空且使用共享正文；版本递增、撤回/过期和 downstream JSON 引用可查询。该批与 Context/D4、D5 及运维回归同批共 259 passed。
- 限制：PDF 页数上限 200、解析时限 30 秒，单文件仍受 2MB 限制；扫描件不执行 OCR；Bing/AI 真实联网质量不在本步重复做质量调优。

## 2026-09-11 / D4-01～04：Context、Handoff、Skill、Memory 与 Knowledge（功能完成，最小验证）

- 步骤 ID：D4-01～04。目标：统一模型上下文组装，按角色/来源交接，技能不越权，短期/长期记忆和项目知识可用且可审计。
- 修改文件：src/harness/runtime/run_context.py（thread_id/context_budget/memory·knowledge·skills开关/handoff_text）；src/harness/context/builder.py、compressors.py（来源摘要/截断标记、tool 配对修剪）；src/graph/agent_loop.py（context_composer 与工具 allowlist）；src/harness/runtime/agent_runtime.py（SkillRegistry/route/inject、LongTermStore、知识检索、thread checkpointer、context.json、显式记忆写入）；src/harness/model_gateway.py（普通非工具模型调用统一过 Context Builder）；src/harness/skills/router.py（可选择禁用 LLM rerank）；tests/test_d4_integration.py。
- 验证（目标性）：Context 中包含 goal/constraints/handoff/skill/memory/knowledge；技能选择与理由入 context.json；技能工具权限取入口权限与技能 allowed_tools 交集；thread_id 下第二轮可见第一轮历史；显式“记住”可保存、查看、删除，memory_enabled=false 时既不写入也不召回；长历史与 tool request/response 可配对。该批与 D3、D5 及运维回归同批共 259 passed。
- 限制：Agent Runtime 路径已统一走 Context Builder；研究链的固定阶段提示词通过 model_gateway 的统一重组，但技能/记忆目前只在 Agent Runtime 路径注入；知识库仍是项目文件 BM25-lite，不是向量库；UI 尚未提供记忆管理页面，D9 再接。
- 下一步：D5-01 任务理解、目标/交付要求与关键条件。'

## 2026-09-11 / D5-01～04：任务理解、能力选型、依赖校验与重规划（功能完成，最小验证）

- 步骤 ID：D5-01～04。目标：理解工作类型和关键条件；按真实能力/工具/网络/预算选型；程序校验依赖和非法计划；重规划保留版本与失效关系且能检测无进展。
- 修改文件：新增 src/harness/planning/understanding.py、capabilities.py、tests/test_d5_integration.py；src/application/orchestration/plan_contract.py（依赖环路 DFS）；scheduler.py（理解结果和完整能力目录进入 plan_meta）；executor.py（fanout 拓扑依赖派工，缺前置不执行）；src/interfaces/cli.py（待输入、waiting_input、能力过滤和显式模式安全降级）；src/harness/planning/task.py（Plan version/parent_version/invalidated_task_ids）；replanner.py（失效下游、版本递增）；executor.py（重复计划指纹 no_progress）。
- 验证（目标性）：任务类型与 material gaps/key conditions 分类；真正缺对象时 input_request.json 可持久化恢复；能力目录拒绝未实现模式；依赖循环被拒绝；前置失败时下游不执行；重规划只重排失效下游并保留无关完成任务；重复重规划标记 no_progress；调度元数据带理解结果与能力目录。
- 限制：D5 的能力目录只把 fixed/fanout 标为已实现，其他四种模式仍由 D6 逐项开放；能力过滤目前使用入口提供的工具/模型/网络/预算概况，不做运行时价格波动预测；待输入状态已持久化，完整 Web 恢复交互在 D9。
- 下一步：D6-01 single 统一根任务。
''

## 2026-09-11 / D6-01～03：single、fixed、manager_worker 统一接入（功能完成，最小验证）

- 步骤 ID：D6-01～03。目标：三个已实现模式从统一能力目录和执行器进入同一根任务，保留输入、角色、预算与结构化交接。
- 修改文件：src/application/orchestration/plan_contract.py（FIRST_VERSION_MODES 扩为 single/fixed/manager_worker/fanout）；scheduler.py（能力/工作类型启发式选型与四种计划构造，提示词同步）；executor.py（single 根任务、manager_worker 依赖链、上游 final_text 作为下游前置产出）；src/harness/planning/capabilities.py（开放四种模式）；src/interfaces/cli.py（模式参数与显式模式入口）；tests/test_d6_integration.py；tests/test_orchestration.py（角色集合纳入 editor）。
- 验证（目标性）：single 只运行一个根任务；fixed 保持原固定链；manager_worker 生成 researcher→organizer→writer 依赖链，研究产出实际进入组织任务，组织产出进入根成稿；能力目录只开放四种已实现模式；既有 planning/orchestration/研究入口回归通过。子批次合计 82 passed。
- 限制：本子批次尚未实现 fanout 真并发、dynamic_team/debate 和二层嵌套派工；这些保持 D6-04～07。
- 下一步：D6-04 fanout 在根预留下真实有界并发。
''

## 2026-09-11 / D6-04：fanout 真实有界并发（功能完成，最小验证）

- 步骤 ID：D6-04。目标：在根预算预留协议下让依赖无冲突的子任务真实重叠执行，同时保留成功/失败分支、角色配置与结构化引用。
- 修改文件：src/application/orchestration/executor.py（fanout 改为依赖波次调度；ThreadPoolExecutor 受 plan.max_parallel 限制；每个子任务启动前 root_ledger.reserve，完成后 settle；所有子结果仍写 orchestration.json；上游结果继续传递给下游）；tests/test_d6_integration.py（受控慢 Worker 并发重叠测试）。
- 验证（目标性）：两个 0.12 秒子任务的最大同时在跑数达到 2；manager_worker 依赖交接仍通过；预算预留/结算、共享来源、子引用、失败分支和既有编排回归通过。D6-04 并发与编排回归 38 passed。
- 限制：并发上限受计划和根预算约束；真实模型供应商的并发限流仍由 Q2 实测后调参；子任务取消传播待 D8 统一收口。
- 下一步：D6-05 dynamic_team。
''

## 2026-09-11 / D6-05：dynamic_team 动态组队（功能完成，最小验证）

- 步骤 ID：D6-05。目标：初始资料发现缺口后，可在重规划与派生上限内调整角色/任务，同时不绕过根预算、来源和结构化交接。
- 修改文件：src/application/orchestration/plan_contract.py、scheduler.py、executor.py；src/harness/planning/capabilities.py；tests/test_d6_integration.py、tests/test_d5_integration.py、tests/test_orchestration_s8.py。
- 实现：dynamic_team 进入能力目录；执行器在根 job_scope 下复用 plan_task/replan，动态子任务按有界并发执行；每次角色调用先 reserve、完成 settle，并写入结构化子结果；规划/重规划调用计入根账本；根成稿继续复用共享来源和子结果。
- 验证（目标性）：动态计划被选用，动态 Worker 结果回写 subtasks，预算/来源回链和既有 planning/orchestration 回归通过。D6-05 子批次 53 passed。
- 限制：dynamic_team 的重规划上限仍使用 planning 的 max_replans；缺口语义合并属于 D7。取消传播在 D8 统一收口。
- 下一步：D6-06 debate。
''

## 2026-09-11 / D6-06～08 + D7-01～02：六模式收口、嵌套与引用谱系（功能完成，最小验证）

- 步骤 ID：D6-06 debate、D6-07 嵌套、D6-08 统一注册、D7-01 引用谱系、D7-02 多交付类型。
- 修改文件：src/application/orchestration/registry.py（六模式处理器）；executor.py（debate 双子任务并发、统一注册分派、引用谱系落盘）；scheduler.py、plan_contract.py、capabilities.py（六模式开放与选型）；src/harness/tools/subagent.py（深度/总量/同题去重/权限继承）；src/harness/runtime/agent_runtime.py、src/application/research.py（子智能体工具接线）；refs.py（EvidenceRef 原始来源字段与 build_root_lineage）；src/application/request.py、pipeline/model.py、pipeline/runner.py（delivery_kind 与 collection/analysis/report 分支）；tests/test_d6_integration.py、tests/test_d7_integration.py。
- 验证（目标性）：debate 双方并发并产生审查清单；嵌套超过二层、同题重复和总量超限均被拒绝，权限按父运行继承；六模式均可通过统一注册表分派；根报告证据按摘录映射到子证据及原始来源；collection 在素材包后直接交付，analysis 走精简章节，report 保持完整固定链；显式必需章节不会被短交付路径绕过。定向批次 97 passed，D3～D7 联合回归 279 passed。
- 限制：引用谱系按摘录前 80 字匹配，语义改写后的证据仍由 D7-03/审校处理；debate 裁判是模型审查，不是独立事实裁决；嵌套子运行仍使用 AgentRuntime 的工具权限快照，完整跨进程取消/审批在 D8。
- 下一步：D7-03 运行中检查与有限补搜/补派/修订。
''

## 2026-09-11 / D7-03～04 + D8-01～03：有限补做、来源更新与恢复状态（功能完成，最小验证）

- 步骤 ID：D7-03 有界补做、D7-04 来源更新改稿、D8-01 持久父子/计划状态、D8-02 取消传播、D8-03 恢复审计。
- 修改文件：src/application/pipeline/runner.py（一次 repair_callback 补源→重抽证据→更新素材）；src/application/research.py（联网补搜回调、revise_with_source_update、resume_notes）；src/harness/state/db.py（schema v2：parent_job_id/plan_version/stage_history_json）；queue.py（父子登记、计划版本和阶段历史）；resume.py（可复用来源/阶段/子结果/预算历史/unknown 操作审计）；orchestration/executor.py（state_queue、should_stop、波次间取消）；tests/test_d8_integration.py。
- 验证（目标性）：明确缺口触发一次补做并新增来源；撤回来源后只用剩余资料生成新 job，旧稿保留且 source_update.json 记录谱系；父子任务和计划版本持久化；取消后后续分支不启动；恢复审计保留 unknown 操作、历史调用和未知费用。定向批次 50 passed，D3～D8 联合回归 325 passed。
- 限制：补做固定最多一次，完整“缺口→补搜→复验”策略调优留 Q3；来源更新当前重跑受影响研究链而非按章节最小重算；unknown 操作只审计不自动重放。
- 下一步：D8-04 待输入与审批持久化。
''

## 2026-09-11 / D8-04 + D9-01～04：待输入持久化与统一工作台（功能完成，最小验证）

- 步骤 ID：D8-04、D9-01～04。
- 修改文件：src/harness/state/db.py（schema v3 + pending_inputs）；pending_inputs.py（目标/参数指纹/预算/计划版本/到期/回答/失效）；queue.py（未完成等待项可合并补充参数后重新入队）；src/interfaces/web/workbench.py（waiting_input、plan_only、待输入回答、HTML/过程导出、方式与状态展示）；tests/test_d9_integration.py、test_state_core.py。
- 验证（目标性）：待输入可持久化、回答、过期和被变更失效；审批仅 granted 可放行，过期/变更不能重放；Web 只看计划不创建任务；缺对象任务进入 waiting_input，回答后重新排队；HTML 和过程记录可下载；工作台显示实际方式、阶段、取消和待输入。定向批次 41 passed，D3～D9 联合回归 333 passed（高负载偶发 workbench 用例单跑通过后完整复跑通过）。
- 限制：页面仍是标准库基础可读实现，视觉和交互优化留 Q3/Q4；本轮未做真实浏览器全旅程验收。
- 下一步：D10-01 安装/启动/配置诊断/备份恢复与依赖迁移说明。
''

## 2026-09-11 / D10-01～04 + Q1-01：交付装配、功能冻结与全量离线回归

- 步骤 ID：D10-01 安装运维、D10-02 MCP、D10-03 评测/冻结工具、D10-04 候选版清点、Q1-01 全量离线回归。
- 修改/新增：scripts/install、start_workbench、backup、restore；src/ops/restore.py 与 restore_cli；docs/INSTALL_AND_RECOVERY.md；docs/MCP_USAGE.md；src/mcp/bootstrap.py 子进程 UTF-8；eval/freeze_manifest.py；eval/q1_regression.py；docs/FEATURE_FREEZE.md；tests/test_d10_integration.py。
- D10 验证：备份恢复往返、MCP 授权工具发现、冻结清单哈希和文档/脚本存在；定向 13 passed。freeze_manifest 检查所有 D 步骤功能完成，冻结 151 个源码/配置文件、依赖哈希和数据集版本。
- Q1-01：执行 `.venv\Scripts\python -m eval.q1_regression`，全量 pytest 506 passed / 0 failed，用时 63.18 秒；报告 eval/reports/q1_offline_regression.json/.md。未跳过失败、未隐藏失败。
- 限制：Q1-01 是离线全量回归，不等于 Q1-02 故障注入、Q1-03 真实浏览器/安装验收、Q2 真实业务质量或 Q4 试用验收。
- 下一步：Q1-02 故障注入与对账。
''

## 2026-09-11 / Q1-02～03：故障矩阵与真实浏览器验收

- Q1-02：新增 eval/q1_faults.py；14 类故障（10 类既有 + 并发预留/子任务取消/未知操作/审批过期）各两轮，28/28 通过，报告 eval/reports/q1_fault_matrix.json。
- Q1-03：修复工作台内嵌 JS 换行转义；隐藏启动真实浏览器会话，完成 agent `6*7` 结果展示、真实模型研究任务（合成资料、deepseek-v4-flash、collection.v1、2 引用、accepted）、失败状态显示、刷新与服务重启历史保留、Markdown/HTML/过程记录链接。`src.ops.verify` 通过；MCP 与备份恢复测试通过。报告 eval/reports/q1_browser_install.json/.md。
- 限制：Q1-03 是功能旅程验证，不是质量评分；Q2-01 真实 99 次批次需要预算授权与人工评分，Q4-01 连续 7 天试用无法由本轮代码替代。
- 下一步：Q2-01；执行前确认预算和人工评分负责人。
'';

## 2026-09-11 / Q2～Q4 用户自测工具链准备

- 新增 eval/web_baseline.py 与 eval/datasets/web_topics_v1.json：12 个公开主题，六模式各至少 2 个；真实联网批次使用统一 CLI 入口并保存来源/引用谱系。
- 新增 eval/q2_summary.py、eval/q3_compare.py、eval/trial_log.py、eval/q4_signoff.py：业务/联网汇总、同条件前后对比、7 天 20 任务试用日志、最终签收草稿检查。
- 新增 scripts/q2_real.ps1、q2_web.ps1、q2_ingest.ps1、q3_compare.ps1、q4_trial.ps1、q4_signoff.ps1 和 docs/Q2_Q4_USER_RUNBOOK.md。
- 验证：工具结构、汇总/对比/试用状态和文档脚本存在，18 项离线工具测试通过；未执行真实收费批次、人工评分或 7 天试用。
'