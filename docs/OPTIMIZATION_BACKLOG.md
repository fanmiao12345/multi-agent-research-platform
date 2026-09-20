# 整体测试与优化待办

更新：2026-09-11。排期以 [PROJECT_MASTER_PLAN.md](PROJECT_MASTER_PLAN.md) 为准。

本清单用于把非阻塞质量/效率/体验问题集中放到 Q 阶段。D 阶段不据此反复调参或批量评测。尚未实现的必要功能仍留在开发清单，不能塞入此处规避功能冻结门槛。

## 记录格式

ID / 问题与用户影响 / 证据位置与版本 / 归属 Q 阶段 / 处理状态 / 修复与前后对照 / 剩余限制。

以下均为待整体复核的问题或评估任务，不代表本次重新运行或确认了历史实验结论。

| ID | 问题或评估任务 | 当前依据 | 排期 | 状态 |
|---|---|---|---|---|
| O-01 | 链内 accepted 与独立业务质量可能不一致，尤其事实支持和保守分级 | IMPLEMENTATION_LOG 的历史真实批次；需以新冻结版本闭卷复核 | Q2-01、Q3-01 | **Q2-01 已给出冻结版基线（2026-09-16）**：链内 accepted 80/99 中独立评测 accept 49/80 = 61.3%；预期行为符合率 68/96 = 70.8%（门槛 90%，未达标）。待 Q3-01 定位修改 |
| O-02 | 搜索相关性、原始来源覆盖、正文成功率及查询次数的取舍 | D3 功能已接通，但尚无新冻结版本的真实质量基线 | Q2-02、Q3-01 | 待建立基线（Q2-02 未执行） |
| O-03 | 六模式的适用范围、自动选型质量与额外调用成本 | 当前仅初版 fixed/fanout，不能据此推断所有模式收益 | Q2-02、Q3-02 | 待建立基线（Q2-02 未执行） |
| O-04 | 默认预算中点、成稿预留、并发与模型路由参数 | 用户已定中点规则，参数需要同等任务规模下的实际成本校准 | Q3-02 | **Q2-01 已提供成本样本**：99 次真实运行 $4.71（单次均值 ≈$0.048）、时延 mean 220s/p95 369s；待据此校准 |
| O-05 | 报告完整性、冗长程度、长资料上下文取舍与改稿效率 | 历史记录有漏项/波动，不能直接作为新候选版表现 | Q2-03、Q3-01 | **Q2-01 已给出四维读数**（链内 accepted 4.15/4.80/4.24/4.45）；改稿分钟仍需人工评分 |
| O-06 | 浏览器流程易用性、详情信息量、查看来源与导出便利性 | D9 先接通必要功能，整体浏览器验证与用户体验集中评估 | Q1-03、Q3-03、Q4 | 待验证 |
| O-07 | 历史工作台测试在高负载下偶发连接错误 | 历史日志记录，后续批次也曾全绿；本次未全量复现 | Q1-01、Q1-02 | 待整体复核 |
| O-08 | **五类具体质量问题**（v1 20 例人工确认批次逐例判读得出，均为 Q3-01 靶子）：① 自造"开放冲突"——把某来源"未提供 X"的证据条目当成与其他来源的冲突（o02/o04/o05/r01/r03/r05/r06/v02）；② 推断/未知证据被标成〔事实〕（全批普遍）；③ 内部标识泄漏进成品（素材包字段名、`src_xxx` 来源 ID；o03/o08/v01）；④ 与事实相反的核心断言（o08 反复称"只提供 1 份来源、第二份缺失"；o07 称"未提供方案甲/乙任何信息"）；⑤ 改稿类未落实"说明删除了哪些结论"（v04，原稿就在任务上下文里） | `eval/reports/gate_real_batch_human_confirmed.json`（20 例人工确认）、`combined_scores.csv` 逐例 note、`.tmp/gate_reasons.txt`；参考读数：四项均≥4 仅 9/20=45%、预期交付一致 12/20=60%、引用均值 3.65、正确性均值 3.70（AI 预填 + 用户认可，`ai_prefilled_human_approved`） | Q3-01 | **程序化完成 + 首轮校准完成（2026-09-16/20）**：③ error（根因修复）；② fact_label 校准后降 warn；③′ 来源数量断言 error；① `unfounded_conflict` warn——**第 4 批校准发现初版口径噪音（9/12 触发，噪音源=无引用冲突陈述句），已收窄为「冲突句须至少引 1 处证据」，离线重放降噪 9→3 例；剩余 3 例（r08/v02/v03）待人工复核本轮产出定真伪**；⑤ `revision_change_note` warn——改稿 4 例命中 3，命中例历史判定 accept 但人工判定对应旧产出，本轮真伪同样待复核。复核表：`eval/reports/q3_batch4_human_review/batch4_review.csv`（12 行，重点 3 例）。剩余：④ 反事实断言（语义判定，保留 source_count 代理）；o08 第 2 批漏报待批次数据再析 | **程序化 + 第4批真实校准 + AI 复核定案（2026-09-16~20）**：③ error（根因修复）；② fact_label 校准后降 warn；③′ 来源数量断言 error；① 初版 `unfounded_conflict`（含缺席子条件）**经第4批12题校准+AI复核证伪**：初版触发 9/12（噪音=无引用冲突套话句），收窄后剩 3 例经逐句复核**仍全部误报**（r08 真矛盾+引用不完整 / v02 缺席与实数据并存恰是真矛盾 / v03 元表述噪音）——**缺席措辞与实数据并存往往正是真矛盾，不能当自造冲突判据**。已改造为 `conflict_under_cited`（冲突声明引用完整性提醒，warn 不封顶，回退记录在 git）；⑤ `revision_change_note` warn 保留（改稿 4 例命中 3，本轮真伪待复核表填写）。复核材料：`eval/reports/q3_batch4_human_review/`（指引含逐条判定+CSV）。剩余：④ 反事实断言（语义判定，保留 source_count 代理）；o08 第 2 批漏报待批次数据再析 ||
| O-09 | `eval.business_eval` 的 `--max-cost` 是**单任务上限**而非批次上限，报告文字却写成"批次限额"，Q2 脚本 `-MaxCost 5` 因此不能真正封顶整批花费（99 次按历史单价约 $6） | `eval/business_eval.py`（max_cost 传给 TaskRequest）与报告文字；`scripts/q2_real.ps1` | Q2-01 前置 | **已修复（2026-09-11）**：新增 `--batch-max-cost` 整批累计上限（上限用尽或未知用量 → 剩余记 not_executed）+ 脚本 `-BatchMaxCost`；Q2-01 实际 $4.71 未触发上限 |
| O-10 | **Q2-01 冻结版暴露的机制级弱点（Q3-01 优先序输入）**：① `refuse_without_evidence` 预期符合 **0/6**——预期"无法完成"却交付成品（r12 类"该拒未拒"）；② `conservative_grading` **1/15**——预期草稿却交成品（r03/o13/r11 类，保守降级缺失）；③ `dedup` **3/9**；④ `gap_declaration` **7/18**；⑤ `controversy_balance` 5/6、`subtopic_decomposition` 4/6 为次弱；⑥ 稳定性：33 例中 10 例三次交付等级不一致（含 r02/r03/r05 出现"未交付"） | `eval/reports/q2_rescore_graded.md`（分机制表）、`business_report_graded.json`、`.tmp/q2_stability.txt`；整体：预期符合 70.8%、质量 61.3%、执行 97.0% | Q3-01（正确性/权限与数据问题优先） | 待优化（有失败样本与分机制分母） |
| O-11 | **联网研究质量（Q2-02 真基线，2026-09-16 限额修复后重跑）**：12 题、六模式各 2 覆盖达标；交付 **3 accepted / 3 draft / 6 unable**（逐模式 accepted：single/fixed/fanout 各 1；manager_worker/dynamic_team/debate 均 0）；**抓取可读率 56/107 = 52.3%**；**引用谱系 0**；0 题预算停止。6 个 `unable` 经逐题核实为**诚实拒绝**（搜到的页面与主题不匹配、或均为产品宣传/词典类）——**真问题是搜索相关性与正文可读率**，不是模式能力。失效的旧基线（8192 token 掐断）归档为 `q2_web_baseline_invalid_8192.json` | `eval/reports/q2_web_baseline.json`（含 `level_distribution`/`budget_stopped`/`mode_accepted`）、`eval/reports/q2_web_workspace/jobs/*`、`.tmp/q2_web_true_baseline.txt` | Q3-01（资料获取与证据质量） | **已验证有效并关闭（2026-09-18 双臂复跑）**：Arm A（纯相关性修复，`SEARCH_BLOCKED_DOMAINS=none`）12 题 = **accepted 5 / draft 2 / unable 5**（基线 3/3/6），可读率 **62.5%**（52.3%），抓取总量 -25%，manager_worker/debate 模式 accepted 破零；单题波动存在（w07 accepted→unable、w12 accepted→draft）。引用谱系三臂均 0（D7 功能缺口另案）。对照证据：`eval/reports/q3_web_after_relevance_only.json` vs `q2_web_baseline_before_relevance_fix.json`；域名降权增量见 O-14 |
| O-12 | **交付等级自述封顶（Q3-01 第 1 批）**：`refuse_without_evidence` 0/6 与 `conservative_grading` 1/15 的根因是"报告自述做不了/证据不足，程序层仍判成品"。已加 `review.delivery_cap()` + runner 接入，命中即封顶并记 `self_declared_cap` | 前后对比复跑 8 题（$0.38，`.tmp/q3_batch1_progress.txt`）：**r03→draft✓、r07→unable✓、r12→unable✓**；**r04 误伤已修正**（建议类交付物不得封顶，复验恢复 accepted✓）；o13 未修（其报告未自述不足，需第 2 批另找信号） | Q3-01 第 1 批 | 部分完成：3/5 受影响案例修复，1 例误伤已修并加防误伤用例；o13 留待第 2/3 批 |
| O-13 | **稳定性：审校阶段偶发非 JSON 输出导致 `failed`**：第 1 批复跑中 r11 在 review 阶段两次解析失败而降为 `failed`（与交付质量无关）；历史上也出现过（r01 审校两次空回复） | `.tmp/q3_batch1_r11.log`、`eval/reports/q3_batch1_r11/business_report.json`；历史见 IMPLEMENTATION_LOG 2026-09-09 真实评测条目 | Q3-01 稳定性专项（优先于费用/速度） | **已实现待真实复现验证**：`runner` 捕获审校 `StageError` → 记 `reviewer_unavailable`、保留已写好的稿子按草稿交付（不再 `failed` 丢稿），单元测试 `test_pipeline_reviewer_unavailable_keeps_draft` 通过；是否真实命中要看复跑时模型是否再次输出非 JSON（第 2/3 批未复现） |
| O-14 | **抓取被反爬拦截（可读率的真因，非解析问题）**：联网批 107 份来源里 `read_failed` 38 份（35.5%），逐条看 `status_message` **全部是 HTTP 403**（baike.baidu.com、zhihu.com、csdn、gov.cn 等），抓取器 UA 为 `agent-mvp/0.1 (local research import)`；`ok` 的 56 份提取质量正常（正文中位 4.2k 字、字符/字节 0.446），所以 52.3% 可读率不是解析缺陷 | `eval/reports/q2_web_workspace/jobs/*/sources/*.meta.json`（`status_message`）、`src/harness/ingest/url_policy.py`（`user_agent` 默认值） | Q3-02 联网专项 | **用户决策：B 方案（保持诚实 UA + 降权换源），已实施（2026-09-18）并经扩大样本定案（2026-09-20）**：`site_policy.py` 静态名单+任务内动态 403 实录，接线三条搜索路径。**六样本定案（每臂 36 题）**：accepted 率 B 30.6%（11/36） vs A 27.8%（10/36），两比例 z 检验 **p=0.795 无显著差异**——成稿质量等价；可读率 B 63.2% 略优、且少抓 403 站。**B 方案保留为默认**；单轮波动实锤（同配置轮间差 3 题 > 臂间差 1 题，单轮结论不可信）。证据：`eval/reports/q3_web_six_sample_summary.json` |
| O-15 | **compose_context 的 messages 份额被 allocate 归一化挤占（Prompt Cache 收益的结构性上限）**：`allocate()` 对传入权重重新归一化，扣除 messages/reserve 后剩余权重合计 0.7 被放大回 1.0，`messages_lim = total - sum(limits) ≈ 0~2` → `keep_last` 恒为 2 条。后果：①多轮对话的既有历史几乎不进 prompt（上下文连续性受损）；②记忆注入 User Message 的前缀稳定收益没有体现载体（`eval/resume_metrics` 口径 4 实测两种注入位置增益 ≈0） | `src/harness/context/builder.py`（compose_context）、`src/harness/context/budget.py`（allocate 归一化）、`eval/reports/resume_metrics/resume_metrics.json`（口径 4 读数） | ~~Q3-02/Q4 之间~~ **已切换（2026-09-20）** | **已修复并切换默认**：`RuntimeContext.reserve_message_window` 默认 True（Q3 联网双臂批次收尾后启用），收益开关对照 +28.9pp（user 71.3% vs system 42.4%）；切换后全量 616 项回归通过（仅更新旧默认断言 + 1 例已知 Windows 偶发隔离复跑过），真实冒烟一题联网研究 accepted（6/8 来源、206s）。回退口：置 False 即恢复旧行为 |

以下是功能缺口，不属于本清单的调优任务：子报告到原始证据的完整映射、真实并发/嵌套派工、Web/CLI 统一自动选型、工作台记忆管理页面。分别按 D6～D9 实施。

## 处理纪律

1. D 阶段发现影响本步可运行性、数据/权限正确性的缺陷，转当前步骤修复并记录理由。
2. Q 阶段先冻结和测基线，再定位问题、修改、复验；保留失败样本与未知成本，不修改参考答案迎合结果。
3. 性能改善必须同时说明质量和费用变化；无收益如实记录。
4. 新的必做功能须回到主计划确定范围；不能用“优化”无限扩项。
