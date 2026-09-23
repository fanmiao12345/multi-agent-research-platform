# Q4-01 七天试用执行计划（2026-09-22 ～ 2026-09-28）

版本：2026-09-22；修订：2026-09-23（D1 阻塞修复后，见 §3.1）。配套：`docs/Q4_TRIAL_GUIDE.md`（操作手册）、
`docs/TRIAL_LOG_TEMPLATE.md`（日志表）、`scripts/q4_trial.ps1`（记录助手）。

## 0. 口径声明（先写清楚，避免验收时口径混乱）

- 按用户 2026-09-22 指示：**任务由 AI 生成、材料由 AI 从公开来源抓取**，AI 担任操作员。
- 因此本批记录的真实性口径是：**任务真实执行、材料真实来源、结果真实产出**，
  但**不是用户本人日常工作任务**——这与 `eval/trial_log.py` 策略字段
  （"必须是真实个人任务；不能由自动批次冒充"）存在口径差异。
- 结论：Q4-03 签收时必须如实标注该口径，**不得**把本批记录表述为"用户个人真实使用"。
- 试用期内发现的问题只登记、不改业务代码（红线 3），统一走 Q4-02。

## 1. 材料清单（真实来源，已抓取，落地在 `workspaces/_trial_inputs/`）

该目录被 `.gitignore` 覆盖（`workspaces/`），不入版本库。

| 文件 | 来源 URL | 用途 |
|---|---|---|
| m1_llm_wikipedia.txt | https://en.wikipedia.org/wiki/Large_language_model | P1 背景 |
| m2_transformer_wiki.txt | https://en.wikipedia.org/wiki/Transformer_(deep_learning_architecture) | P1 背景 |
| m3_attention_paper.txt | https://arxiv.org/abs/1706.03762 | P1 原始论文 |
| m4_vllm_readme.txt | https://raw.githubusercontent.com/vllm-project/vllm/main/README.md | P1 推理框架 |
| m5_ollama_site.txt | https://ollama.com/ | P1 推理框架 |
| m6_llamacpp_readme.txt | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/README.md | P1 推理框架 |
| p2_llama2_2307.09288.txt | https://arxiv.org/abs/2307.09288 | P2 技术报告 |
| p2_qwen2_2407.10671.txt | https://arxiv.org/abs/2407.10671 | P2 技术报告 |
| p2_mixtral_2401.04088.txt | https://arxiv.org/abs/2401.04088 | P2 技术报告 |
| p2_deepseekv3_2412.19437.txt | https://arxiv.org/abs/2412.19437 | P2 技术报告 |

当天按需再抓取：T16/T17 的联网检索、T18 的第三来源材料、T10/T17 的"待核查宣称"。
被搜索策略屏蔽、不可作为来源的域名：`baike.baidu.com`、`csdn.net`、`wenku.baidu.com`、`zhihu.com`。

## 2. 划分逻辑

共 **21 个任务 / 7 天 / 每天 3 个**，按真实工作节奏排成"闭环日"：

- ① **素材类**（资料整理）：低风险，先把原材料变成结构化素材；
- ② **产出类**（研究写作 / 核查）：基于①产出正文或核查结论；
- ③ **收敛类**（改稿 / 单点问答）：对①②做压缩、补强或校验，天然产生 `--revise-job` 改稿链。

每天 ① 产出给 ② 用，② 产出给 ③ 改，跨天再用 `--revise-job` 复用上一篇（旧稿保留）。
Day 1 故意放最简单任务开局（含 1 个单点问答）先验证链路；Day 7 收口归档。

任务类型分布：资料整理 6、研究写作 6、核查 2、改稿 5、单点问答 2（问答占比 10%，符合"占比别太高"）。

## 3. 任务清单与七天划分

通用命令模板：

```powershell
.venv\Scripts\python -m src.interfaces.cli "<任务描述>" --mode real --flow research `
  --import-file <材料文件> [--allow-network]
```

> 2026-09-23 起**不需要再传任何预算参数**：`max_calls` 12→40、`max_output_tokens` 8192→65536
> 已是出厂默认（研究链口径，依据见 `docs/BUDGET_CALIBRATION.md` §五）；费用 $0.15、时间 600s
> 护栏不变。显式传参始终优先——传了不报错，但没有必要。

| # | 天/日期 | 类型 | 任务 | 输入 | 预期产出 |
|---|---|---|---|---|---|
| T01 | D1 09-22 | 资料整理 | P1 资料目录：标题/日期/类型/用途 + 覆盖范围（D1 draft，**D2 补跑收口**） | m1–m6 | 目录 + 来源表 |
| T02 | D1 09-22 | 单点问答 | token 单价换算成"每百万 token 成本" | 无 | 数值结果 |
| T03 | D1 09-22 | 研究写作 | P1 综述初稿《本地部署开源大模型的选型要点》（带引用）（D1 draft，处理见 §3.1） | m1–m6 + 联网 | 综述初稿 |
| T04 | D2 09-23 | 资料整理 | P1 关键事实表：框架/许可/硬件门槛/适用场景 | m1–m6 | 事实表 |
| T05 | D2 09-23 | 改稿 | 把 T03 压到 300 字以内决策摘要 | `--revise-job <T03 job_id>`（见 §3.1） | 短摘要 |
| T06 | D2 09-23 | 研究写作 | P2 四篇技术报告要点摘录 | p2 四份 | 要点摘录 |
| T07 | D3 09-24 | 资料整理 | P2 对比表：参数/上下文/许可/数据/发布时间 | p2 四份 + T06 | 对比表 |
| T08 | D3 09-24 | 改稿 | P2 对比改成表格化决策摘要 | `--revise-job T06` | 决策摘要 |
| T09 | D3 09-24 | 研究写作 | 《vLLM / Ollama / llama.cpp 各自适合谁》 | 联网 | 对比分析 |
| T10 | D4 09-25 | 核查 | 核对一条公开性能宣称的出处与口径 | 当天抓取 | 有据/无据结论 |
| T11 | D4 09-25 | 改稿 | 给 T03 综述补"局限与反方观点" | `--revise-job <T03 job_id>`（见 §3.1） | 补强稿 |
| T12 | D4 09-25 | 资料整理 | 把 T09 的检索结果整理成资料目录 | T09 结果 | 检索目录 |
| T13 | D5 09-26 | 研究写作 | 主流开源模型发布时间线综述 | p2 + 联网 | 时间线综述 |
| T14 | D5 09-26 | 改稿 | 时间线改成 5 条要点 | `--revise-job T13` | 5 条要点 |
| T15 | D5 09-26 | 单点问答 | 一个业务算式（成本/时延折算） | 无 | 数值结果 |
| T16 | D6 09-27 | 研究写作 | 《家庭分时电价是否划算》联网分析 | 联网 | 分析报告 |
| T17 | D6 09-27 | 核查 | 该主题下识别非独立来源（同一数据多方转载） | T16 来源 + 当天抓取 | 来源独立性结论 |
| T18 | D6 09-27 | 资料整理 | 三份公开材料去重与冲突登记 | 当天抓取 3 份 | 去重表 + 冲突登记 |
| T19 | D7 09-28 | 改稿 | 合并 T03+T11 成最终技术简报 | `--revise-job <T11 job_id>` | 最终简报 |
| T20 | D7 09-28 | 研究写作 | 缺口任务：资料不足时如实说明缺什么（不编造） | p2 部分材料 | 缺口清单 |
| T21 | D7 09-28 | 资料整理 | 七天证据归档 + 引用可定位抽查 | 全周期产物 | 归档 + 抽查表 |

## 3.1 D1 执行结果与阻塞修复（2026-09-23 补记）

D1（09-22）实际结果：**T02 accepted；T01 / T03 均 draft（无最终产物）**，另有 T03r / T03r2 / T03r3
三条诊断与修复验证记录（不计入 21 个计划任务的验收口径）。根因定位与修复详见
`docs/IMPLEMENTATION_LOG.md` 2026-09-23 条目（三层：调度选型 schema 缺陷、研究链预算口径错配、
推理模型隐形思维链），对执行计划的影响如下：

1. **T01（D1 draft）**：D2 补跑收口——同 m1–m6 输入，修复后约几十秒 / 不足 $0.02，与 T04 同批材料。
2. **T03（D1 draft）**：修复验证复跑 T03r3（job `job_a45919d755e04a898be374955470c63f`）链内双层审校
   accepted（71 处引用 0 未解析、2683 字、19 次调用 / 21,723 token / $0.0396 / 99.7s），但
   `business_acceptance=not_evaluated`——**尚未经人工确认**。两个选法：
   - ①（推荐）D2 重跑 T03（同 D1 命令，约 $0.04 / 100s），人工审稿并按试用口径记录干预/修订分钟，
     作为后续改稿基线——这是"真实个人任务"的干净闭环；
   - ② 直接人工审 `job_a45919d7…/artifacts/report.v2.md`，认可则以其为 T03 原稿（省一次跑，
     但该 job 干预分钟为 0，试用口径不如重跑干净）。
3. **T05 / T11 / T19 的 `--revise-job` 落地时必须指向实际 job_id**：D1 原始 T03 job
   （`job_0b2396c3…`）没有报告产物，改稿入口会直接拒绝；用重跑后（或 T03r3）的 job_id。
   `--revise-job <T06/T11 job_id>` 同理——运行时以当天任务的实际 job_id 为准。

## 4. 每天收尾（固定动作）

```powershell
# 每个任务完成后记一条（脱敏）
powershell -ExecutionPolicy Bypass -File .\scripts\q4_trial.ps1 -Command add `
  -Task "T01 P1资料目录" -Result "accepted，引用可定位" `
  -InterventionMinutes 5 -RevisionMinutes 10 -Notes "备注"
# 当天结束看进度
powershell -ExecutionPolicy Bypass -File .\scripts\q4_trial.ps1 -Command status
```

## 5. 风险与限制（开局已知）

1. 联网研究依赖 `SEARCH_PROVIDER=bing_scrape` 是否可用——T03/T09/T13/T16 才需要；
   T01/T02 不联网，先验证链路。若抓取失败，按 `unable` 如实记录，不伪造来源。
2. 本机执行 Python 需在沙箱外运行（沙箱内进程启动被拒绝）。
3. 手册 §0② 备份命令缺 `--out`，实际执行需补 `--out workspaces\_backups`（已按正确用法执行）。
4. 2026-09-20 起的既有工作区产物（约 210 MB）不动；试用材料单独放 `workspaces/_trial_inputs/`。
5. ~~T02 已于 2026-09-22 作为 real 模式端到端冒烟提前执行并记入日志，当晚 21:00 的自动批次只需跑 T01 + T03~~
   → 已执行：T02 accepted；T01/T03 draft，阻塞已于 2026-09-23 修复并真实验收通过（见 §3.1）。
6. **检索相关性（开局已知，O-11 口径）**：D1 实测 bing_scrape 对「显存要求」类查询会返回门户首页级
   噪声 URL（如 gitee/github 组织主页）。T09/T13/T16 等联网任务若再遇到，按 draft/unable 如实记录，
   不修改业务代码，试用期继续积累读数。
7. **thinking 参数兼容性（2026-09-23 新增依赖）**：结构化短输出阶段依赖端点接受
   `thinking:{"type":"disabled"}`；端点拒绝时 provider 自动退回一次不带参数的调用（只多花 token，
   不影响正确性）。试用期内若更换 `MODEL_NAME`/`MODEL_BASE_URL`，注意首跑核对账本里的
   completion_tokens 是否回到正常水位（O-16 现象=单次调用数千 token 而可见内容很短）。
