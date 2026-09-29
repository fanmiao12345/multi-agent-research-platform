# 公开评测数据集候选调研（PUBLIC_DATASETS_CANDIDATES）

生成日期：**2026-09-29**。

## 口径与诚实边界（先读）

- 目的：为研究写作评测补充**真实材料**数据源（当前 `eval/datasets/research_writing_v1.json` 全部为合成数据，`meta.data_policy` 已注明），按"机制→数据集能力"逐项对照。
- **规模/许可证以官方页为准**。本文档信息核实方式：GitHub 官方仓库与 GitHub API 的 license 字段、官方数据站（fever.ai、hotpotqa.github.io、rajpurkar.github.io 等）、arXiv 摘要页。**HuggingFace 站点在本次调研环境中被限流（429/403）无法直接打开**，凡引自 HF 页面的字段均标注"（HF，搜索快照）"，接入前必须重新打开数据页核对。
- 查不到或交叉不一致的字段一律写 **未核实**，不编造。许可证字段为 `null` / `NOASSERTION` / `Other` 的仓库如实列出，视为"未提供明确许可证"。
- 本文只是候选调研，**未接入、未运行、未验收**。任何数据集进入评测前须过 `python -m eval.research_cases` 校验与真实批次验证；未验证不能标已验收。
- 语言口径：项目中文为主。英文数据集可测鲁棒性，但"英文材料 + 中文请求"是跨语言场景，接入时应在案例上单独标 `lang` 字段并在报告分组统计，不得与中文批次混算。

---

## 一、机制 → 数据集 映射总表

| # | 项目机制 | 第一梯队（推荐先接） | 第二梯队（可选） | 不建议（现阶段） |
|---|---|---|---|---|
| 1 | 证据定位引用（逐字 quote） | **CMRC 2018**（中）、**QASPER**（英）、**FEVER**（英） | HotpotQA、MuSiQue、2WikiMultihopQA、QuALITY | KILT（重） |
| 2 | 多来源整理与去重 | **MultiNews**（英） | ScisummNet / CL-SciSumm（英）、WCEP（英，下载受限） | — |
| 3 | 冲突检测与归属 | **CFEVER**（中） | CHEF（中，许可证未注明）、FEVER（英，REFUTES 构造冲突对） | — |
| 4 | 无据拒答 | **DuReader_robust**（中）、**SQuAD 2.0**（英）、**MuSiQue-Full**（英） | QASPER no-answer 子集 | DuReader 2.0 原版（无答案标注口径未核实） |
| 5 | 保守分级/缺口声明 | **FEVER/CFEVER 的 NEI→draft 映射** | ASQA（歧义问题）、QASPER 部分可答 | — |
| 6 | 时效性 | **FreshQA**（英，按周更新版本快照） | — | — |
| 7 | 联网研究 | **WebCPM**（中，需真实搜索接口） | DuReader-retrieval（口径偏检索器） | **GAIA**（重型、gated、许可证未核实） |
| 8 | PDF 输入（页码定位） | **TAT-DQA**（中文表格文档） | DUDE（需 RRC 注册）、MP-DocVQA（许可证未核实）、QASPER 挂 arXiv 原 PDF | — |
| 9 | 改稿链 | **IteraTeR**（英；中文公开集缺位） | WikiAtomicEdits（含中文，百万级需抽样，许可证存疑） | — |
| 10 | 资料内指令注入防护 | **InjecAgent**（MIT，1,054 例） | AgentDojo（需工具执行环境，可借 payload 文本） | — |
| 11 | 简单问答稳定性/幻觉 | **Chinese-SimpleQA**（中，CC BY-NC）、**HaluEval**（MIT） | SimpleQA（英，MIT） | — |
| 12 | 语义支持率（citation recall/precision 自动口径） | **ALCE**（MIT，NLI 判分） | RAGTruth（MIT，词级幻觉 span） | — |

---

## 二、Schema 映射通则（所有数据集通用）

目标 schema 见 `eval/datasets/research_writing_v1.json`（v2）：`sources{id,title,date,text,origin,url}` + `tasks{id,category,source_ids,request,sections,facts[{source_id,quote,claim}],conflicts,gaps,forbidden_claims,expected_outcome,batch,mechanisms}`。

| v1/v2 字段 | 公开数据集映射 | 注意 |
|---|---|---|
| `sources[].text` | 数据集提供的材料单元（段落/句子/文档全文/PDF 解析文本） | 闭卷口径：材料随数据集保存，Agent 不联网；`origin` 建议填 `public_dataset:<名字>`，`url` 填数据集内真实来源 URL |
| `facts[].quote` | **evidence span / supporting fact 句 / 抽取式答案**——必须是能在 `sources[].text` 中逐字找到的字符串 | FEVER/CFEVER 的 claim 是**改写句**不是原文，quote 必须改用其标注的 evidence 原句；接入脚本必须做"quote 逐字命中"断言（`research_cases` 已有同类校验） |
| `facts[].claim` | 问题对应的事实点（可用数据集 claim 或问题改写） | — |
| `request` | 数据集问题 + 本项目任务句式包装（"根据材料整理/回答……"） | 改写需留映射记录，避免改语义 |
| `conflicts` | FEVER 的 SUPPORTS/REFUTES 同一实体成对构造；CHEF misconception；或同题多源数据中人工挑互斥对 | 冲突对尽量取自同一数据集同一实体，归属判定可复核 |
| `gaps` / `forbidden_claims` | 无据拒答数据的"问题所问不在材料中"→ `gaps`；HaluEval/RAGTruth 幻觉样本 → `forbidden_claims` | — |
| `expected_outcome` | answerable+材料充分→`final`；NEI/部分支撑→`draft`；unanswerable/时效失效→`unable` | 与现有 28 final / 3 draft / 2 unable 分布衔接 |
| `batch` | 一律 `v3`（新批次标识），不得并入 v1 冻结分母与 v2 | 先例：`meta.extension_note` 的 v2 扩充 |
| `mechanisms` | 按本文表逐例打标，沿用现有标签（`evidence_location`/`refuse_without_evidence`/…） | 供分机制统计比例 |

---

## 三、分机制推荐

### 机制 1：证据定位引用（逐字 quote）

**第一梯队**

| 数据集 | 链接 | 规模 | 语言 | 许可证 | 标注形态 |
|---|---|---|---|---|---|
| CMRC 2018 | 官方页 https://hfl-rc.github.io/cmrc2018 ；仓库 https://github.com/ymcui/cmrc2018 | 约 2 万题：train 10,142 / dev 3,219 / challenge 约 2,000（train/dev 数字见 [CCL 2021 论文转述](https://aclanthology.org/2021.ccl-1.65.pdf)；challenge 数为第三方口径，未核实） | 中文简体（维基百科段落） | CC BY-SA 4.0（[GitHub API license 字段](https://api.github.com/repos/ymcui/cmrc2018)） | 片段抽取式 MRC：**答案即逐字连续片段**，天然满足"quote 逐字命中" |
| QASPER | 论文 [arXiv:2105.03011](https://arxiv.org/abs/2105.03011)（NAACL 2021）；数据 [HF allenai/qasper](https://huggingface.co/datasets/allenai/qasper) | 5,049 题 / 1,585 篇 NLP 论文（摘要口径） | 英文 | CC BY-4.0（HF 数据页标注，**搜索快照未直接复核**） | 每题答案附 **evidence（论文中被高亮的原文片段）**；答案含 yes/no/抽取/摘要式及 no-answer |
| FEVER | 官方 https://fever.ai/dataset/fever.html | 185,445 条 claim（官方页）；shared-task dev 19,998 | 英文 | 默认 **CC BY-SA 3.0**（[官方许可页](https://fever.ai/download/fever/license.html)：逐条遵循对应 Wikipedia 文条许可，缺省回退 CC BY-SA 3.0） | SUPPORTS / REFUTES / NOT ENOUGH INFO；SUPPORTS/REFUTES 附**证据句 ID 列表**（页/句级定位） |

- **接入建议**：CMRC 2018 取 dev 20～30 题，维基段落直接作 `sources`，抽取答案作 `facts.quote`，`mechanisms=["evidence_location"]`；QASPER 取 evidence 为单句/短段的抽取型题 20 例（`expected_outcome=final`），其 no-answer 题分流给机制 4/5；FEVER 取 dev 集成对材料。
- **风险**：QASPER 官方 GitHub 仓库 `allenai/qasper` 已 404（[API 返回 404](https://api.github.com/repos/allenai/qasper)，2026-09-29 查），下载走 HF；FEVER 的 claim 不可直接当 quote；FEVER 材料是维基单条，多来源整理需自行组包。

**第二梯队**

- [HotpotQA](https://hotpotqa.github.io/)：113k 维基多跳问答，**句级 supporting facts**，CC BY-SA 4.0（官方页）。建议只取 distractor 版（10 段候选含 2 篇金标）做"多来源找证据"；支撑句作 quote。风险：多跳题对闭卷单轮偏难，先抽 1 跳式比较题。
- [MuSiQue](https://github.com/StonyBrookNLP/musique)：约 25k 组合式 2~4 跳问题（[TACL 2022 论文](https://aclanthology.org/2022.tacl-1.31.pdf)），仓库 **CC BY-4.0**（GitHub API）；标注含支撑段落。MuSiQue-Full 含 unanswerable 变体（见机制 4）。
- [2WikiMultihopQA](https://github.com/Alab-NII/2wikimultihop)：约 19.2 万题（第三方转述，未核实），仓库 Apache-2.0（GitHub API）；证据是结构化三元组（推理步），转成逐字 quote 需回查原文段落。
- [QuALITY](https://nyu-mll.github.io/quality)：长文档（数千词）选择题 QA，**CC BY 4.0**（官方排行榜页），仓库 [nyu-mll/quality](https://github.com/nyu-mll/quality)。选择题型、无句级证据标注——适合"长材料+关键事实"的整理任务，引用定位需自行补标，故列二梯队。

**不建议（现阶段）**

- [KILT](https://github.com/facebookresearch/KILT)（MIT，GitHub API）：多任务合并基准，依赖全量 Wikipedia 快照作知识源，单机接入重、与"给材料"闭卷口径不匹配；只需单一任务时直接用其上游数据（FEVER/HotpotQA 等）即可。

### 机制 2：多来源整理与去重

**第一梯队**

- [MultiNews](https://github.com/Alex-Fabbri/Multi-News)：大规模多文档新闻摘要（ACL 2019），每例同题多源新闻 + 人工摘要；仓库 license 字段 `NOASSERTION`（GitHub API，2026-09-29 查），规模约 5.6 万对（论文口径，官方 README 未列，未核实）。用法：同题 2~3 篇作 `sources`，同文转载/重复表述需自行挑簇；摘要中的关键句回查原文作 quote。**风险：许可证未明确，先小样使用并记录出处；商用禁令不明。**

**第二梯队**

- [ScisummNet / CL-SciSumm 语料](https://github.com/WING-NUS/scisumm-corpus)：约 1,000 篇 ACL 论文 + 引用网络 + 人工摘要，**CC BY 4.0**（仓库 README）。引用句→原文句的"证据指向"与本项目 [E-编号] 语义接近；英文、学术域。
- [WCEP](https://github.com/complementizer/wcep-mds-dataset)（ACL 2020）：新闻事件簇（Wikipedia Current Events Portal 编辑摘要 + 引用新闻簇），仓库 MIT（GitHub API）。**落地风险大**：README 明言"当前不提供完整数据集下载"，正文需自行从 Wayback Machine 重抓，链接腐化率高——先做可行性试点再决定。

### 机制 3：冲突检测与归属

**第一梯队**

- [CFEVER](https://github.com/IKMLab/CFEVER-data)：中文事实核查，30,012 条人工改写 claim（[arXiv:2402.13025](https://arxiv.org/abs/2402.13025)，AAAI 2024），仓库 **Apache-2.0**（GitHub API）。用法：对同一维基实体取 SUPPORTS/REFUTES 成对 claim → 两个 `sources` + `conflicts` 登记，期望"分别溯源、不取平均"（对应 o06 机制）。**注意：claim 是改写句，quote 用其证据句。**

**第二梯队**

- [CHEF](https://github.com/THU-BPM/CHEF)（NAACL 2022）：中文证据式事实核查，10,000 条 claim、11 主题（搜索快照口径），含 misconception 等细分标签。**仓库无 license 字段（未核实）**，使用前向作者确认或仅作内部研究小样。
- FEVER（英文，同机制 1）：REFUTES 标注天然给出"同实体互斥断言"，可造冲突对；中文提示词适配需注明。

### 机制 4：无据拒答（refuse_without_evidence）

**第一梯队**

- [DuReader_robust](https://github.com/PaddlePaddle/DuReader-robust)：中文阅读理解鲁棒性集，约 1.4 万题，人工构造 answerable / **unanswerable** / distracting 三类（第三方口径，规模未直接核实）；**仅供研究用途**（官方协议）。用法：取 unanswerable 子集 10~15 例，`expected_outcome=unable`，`mechanisms=["refuse_without_evidence"]`；distracting 子集顺带测抗干扰。
- [SQuAD 2.0](https://rajpurkar.github.io/SQuAD-explorer/)：约 15 万题中**逾 5 万题为对抗性不可回答**（官方页），**CC BY-SA 4.0**（官方页）。英文，规模大但可只抽 20 例；题目"形似可答"，正是拒答机制要的形态。
- [MuSiQue-Full](https://github.com/StonyBrookNLP/musique)：多跳场景下的 unanswerable 变体（同上 CC BY-4.0），材料更接近"多来源汇总后仍无据"。

**第二梯队**

- QASPER 的 no-answer 选项（"没有一篇论文回答该问题"）——与论文材料天然搭配。
- DuReader 2.0 原版（[baidu/DuReader](https://github.com/baidu/DuReader)，Apache-2.0）：README 证实真实问题/文章/答案与 Yes/No 极性子集，但**无答案标注口径未在页面核实**，故用其子集 DuReader_robust 代替。

### 机制 5：保守分级/缺口声明（部分支撑 → draft）

- **第一梯队（映射法）**：FEVER/CFEVER 的 **NOT ENOUGH INFO / NEI** 子集直接映射 `expected_outcome=draft`：材料只覆盖问题一部分时，期望"草稿+缺口清单"（对应 r03、gap_declaration 机制）。
- 第二梯队：[ASQA](https://huggingface.co/datasets/din0s/asqa)（歧义事实型问题的长答案，官方代码在 google-research；HF 页许可证**未核实**，HF 本次限流）——歧义问题天然要求"分情形+声明不确定性"；无歧义标注的保守分级中文公开集未发现，继续用合成数据补。

### 机制 6：时效性（timeliness）

**第一梯队**

- [FreshQA](https://github.com/freshllms/freshqa)（Apache-2.0，GitHub API）：动态问答基准，**按周/按需更新、版本化快照**（README： "We update our dataset weekly or upon request."，2026-04-21 版为当前版本），含快速变化知识与假前提问题（[arXiv:2310.03214](https://arxiv.org/abs/2310.03214)；题目总数未在摘要/README 核实）。用法（闭卷口径）：取**旧版本快照**作材料+问题，问"当前值"→ 期望 Agent 声明无法完成/需联网（对应 r07）；或旧新两版对照考"旧信息 vs 需要新信息"。英文；中文提示词适配注明。

### 机制 7：联网研究（bing_scrape → 读正文 → 引用）

**第一梯队**

- [WebCPM](https://github.com/thunlp/WebCPM)（ACL 2023，**Apache-2.0**，GitHub API）：中文交互式网页搜索长答案 QA，交互数据 5,500 例 + 知乎补充约 900 例 + 流水线数据 11 万例（README 口径），数据经 Google Drive 下载。这是与本项目"真实网页搜索→读正文→引用"最接近的中文集。**接入成本中等偏高**：需把 Agent 的 bing_scrape 接到真实搜索流程，再对比其金标支撑事实；建议只取 10 例做试点，材料快照与抓取时间逐例记录（对应验收文档"真实联网评测另行记录实际 URL、抓取时间和正文"）。

**第二梯队**

- [DuReader-retrieval](https://aclanthology.org/2022.emnlp-main.357/)（EMNLP 2022；数据在 [baidu/DuReader](https://github.com/baidu/DuReader) 仓库子目录）：90K+ 查询 / 800 万+ 段落的中文网页检索相关性集（README 口径）。口径偏"检索器评测"（query-passage 相关性），与闭卷写作评测不同，仅用于评估 bing_scrape 召回质量；许可证需按仓库数据协议确认（未核实）。

**不建议（现阶段）**

- [GAIA](https://huggingface.co/datasets/gaia-benchmark/GAIA)（[论文 arXiv:2311.12983](https://arxiv.org/abs/2311.12983)）：466 题中 validation 165 题公开，**gated 数据集**（需申请 HF 权限），**许可证未核实**（HF 本次限流无法打开数据页）。重型通用 agent 基准：题目要求多工具（代码、文件、浏览器）多步执行，与本项目闭卷/预算口径不匹配；如未来要测通用工具链，只取 validation 子集 5~10 例做人工对照即可，不建议进入常规回归。

### 机制 8：PDF 输入（文本型 PDF 页码定位）

**第一梯队**

- [TAT-DQA](https://github.com/NExTplusplus/TAT-DQA)（项目页 https://nextplusplus.github.io/TAT-DQA ，[arXiv:2207.11871](https://arxiv.org/abs/2207.11871)）：真实中文业务表格文档（约 3,067 页、约 6,000 QA，第三方转述论文口径，未逐字核对），离散推理（数字/表格+文本）。仓库 GitHub license 字段为 null，项目页/README 称 **CC BY 4.0**（搜索快照，未直接复核）。用法：取少量原文 PDF 测"文本型 PDF 页码定位"，答案落在具体页/表。风险：文档为扫描/版式混合时依赖解析质量，先验证 PDF 文本层可提取。

**第二梯队**

- [DUDE](https://rrc.cvc.uab.es/?ch=23)（ICCV 2023，[arXiv:2305.08455](https://arxiv.org/abs/2305.08455)）：多格式（含 PDF）文档问答，标注含答案所在页（论文口径）；**CC BY 4.0**（论文），下载需在 RRC 门户注册。
- [MP-DocVQA](https://huggingface.co/datasets/rubentito/mp-docvqa)：多页 DocVQA（答案带页 ID，HF 页面口径）；**许可证未核实**（HF 本次限流）。
- QASPER 论文可按 arXiv ID 自行挂原 PDF，把 evidence span 对齐回 PDF 文本层，得到"研究论文 PDF + 证据定位"的组合用例（自加工，成本可控）。

### 机制 9：改稿链（基于原稿+指令修订）

**第一梯队**

- [IteraTeR](https://github.com/vipulraheja/iterater)（ACL 2022，[论文](https://aclanthology.org/2022.acl-long.250.pdf)，**Apache-2.0**，GitHub API）：约 2.7 万对文档级修订（arXiv/wiki/blog 四域），约 4 万句级编辑，**每处编辑带修订意图标签**（Clarity/Fluency/Style/Paraphrase）。用法：before 作原稿、after 作参考结果、意图标签包装成修订指令 → 测"按指令修订且不覆盖旧版本"。**局限：英文；中文公开改稿链数据集未发现（本轮调研内）**，中文改稿继续依赖合成 v2 案例，英文集只测机制鲁棒性并单独分组。

**第二梯队**

- [WikiAtomicEdits](https://github.com/google-research-datasets/wiki-atomic-edits)：8 语言维基原子编辑，**含中文**（插入 0.7M / 删除 0.4M，README 口径），总计约 4,300 万编辑——远超"规模适中"约束，只能抽样。**许可证存疑**：仓库 license 字段 null，README 的许可行写的是"Query-wellformedness dataset is licensed under CC BY-SA 4.0"（疑似复制残留，未核实），接入前必须向官方确认。

### 机制 10：资料内指令注入防护 / 工具权限

**第一梯队**

- [InjecAgent](https://github.com/uiuc-kang-lab/InjecAgent)（**MIT**，GitHub API）：1,054 个测试用例，17 类用户指令 × 21 种工具，注入 payload 嵌在**工具响应/资料**中，分直接行为篡改与敏感信息套取两类（[arXiv:2403.02691](https://arxiv.org/abs/2403.02691)）。用法（零工具改造）：把 payload 文本插入 `sources[].text`，`request` 为正常整理任务，期望"整理事实、不执行资料内命令、禁语命中 0"——即 instruction_isolation 机制的对抗版。注入语料为英文，可中英混排测跨语言鲁棒性。

**第二梯队**

- [AgentDojo](https://github.com/ethz-spylab/agentdojo)（NeurIPS 2024 D&B；**MIT**，GitHub API）：97 个真实任务、629 个安全测试用例的**动态环境**（邮件/银行/差旅等，[arXiv:2406.13352](https://arxiv.org/abs/2406.13352)）。需要真实工具执行环境，与本项目当前工具面不合；建议只**借用其注入 payload 文本**（同 InjecAgent 用法），不整体运行。

### 机制 11：简单问答稳定性 / 幻觉标签

**第一梯队**

- [Chinese-SimpleQA](https://github.com/OpenStellarTeam/Chinese-SimpleQA)（官方页 https://openstellarteam.github.io/ChineseSimpleQA/ ，[arXiv:2502.19361](https://arxiv.org/abs/2502.19361)）：约 3,000 道中文短事实题，6 大主题 99 子主题（搜索快照口径）。**许可证 CC BY-NC（非商业）**（论文脚注口径，未直接复核）——个人研究评测可用，**不可并入可再分发的数据集文件**。用法：测事实性/幻觉标签错误（如单位、人数、日期张冠李戴），与"关键事实"门槛互补；注意其短事实口径与本项目闭卷口径不同（闭卷下部分题无据，应答"无法完成"而非硬答——出题时要筛选与给定材料匹配的子集）。
- [HaluEval](https://github.com/RUCAIBox/HaluEval)（**MIT**，GitHub API）：35K 样本 = 5K 通用问答幻觉人工标注 + 30K 任务特定（QA/对话/摘要各 10K，基于 HotpotQA/OpenDialKG/CNN-DM 构造，README 口径）。用法：把"幻觉版答案"作为禁语/关键事实干扰项，检验评测端能否把幻觉标出来（测 grader，也测 Agent）。

**第二梯队**

- SimpleQA（OpenAI，[openai/simple-evals](https://github.com/openai/simple-evals)，**MIT**——仓库 Legal 节明确评测逻辑与数据同 MIT；`simple_qa_test_set.csv`，4,326 题，[openreward 口径](https://openreward.ai)）：英文短事实题 + 校准（不知道应弃权）；判分需模型裁判，成本注意。

### 机制 12：语义支持率（自动 citation recall / precision 口径）

背景：主计划要求"语义支持率 ≥95%"，但至今**没有测量口径读数**（RESEARCH_WRITING_ACCEPTANCE §执行补记已注明）。以下基准提供现成的自动口径。

**第一梯队**

- [ALCE](https://github.com/princeton-nlp/ALCE)（EMNLP 2023；**MIT**，GitHub API）：内置 ASQA / QAMPARI / ELI5 三组数据 + **预取回的检索段落包**（README：`download_data.sh` 提供 top-100 DPR/GTR/BM25 检索结果），评测维度为 fluency / correctness / **citation quality**；论文定义 **citation recall（每个陈述句是否被至少一条引文蕴含）与 citation precision（引文是否真正提供支撑）**，用 NLI 模型自动判分（[论文](https://arxiv.org/abs/2305.14683)）。**接入建议**：这就是"语义支持率"最接近的自动测量——把本项目交付报告的 [E-编号] 引用解析回来源文本，逐句跑 NLI，先在 ALCE 自带数据上验证判分脚本，再应用到 v3 批次。**适配成本**：ALCE 管线是英文 NLI 模型；中文报告需要换中文 NLI 模型或双语句对齐后判分，须把口径与阈值写入评测文档后再报"≥95%"。
- 注：ELI5 原版 [facebook/eli5 已于 2023 年在 HF 弃用下架](https://huggingface.co/datasets/eli5)（搜索快照确认弃用通知），实践上用 ALCE 仓库自带的加工版。

**第二梯队**

- [RAGTruth](https://github.com/ParticleMedia/RAGTruth)（**MIT**，GitHub API）：RAG 场景下约 1.8 万条真实 LLM 回复的**词级幻觉 span 标注**（[arXiv:2401.00396](https://arxiv.org/abs/2401.00396) 摘要："nearly 18,000 naturally generated responses"）。用法：评测端幻觉 span 检测的对照集（测 grader 的漏报/误报），口径是词级不是句子级，作补充。

---

## 四、落地路径建议（小规模真实材料批次，v3 增补）

原则：**v1 的 20 例冻结分母不动**（跨批次对比仍用该子集）；新公开数据全部进 `batch="v3"`，并在 `meta.extension_note` 登记先例（参照 v2 扩充）。以下三批均可单机完成，无训练，只有转换脚本 + 评测调用。

1. **批次 A（中文证据定位 + 无据拒答，约 30 例，最先做）**
   - CMRC 2018 dev 20 题（answerable → `final`，quote=抽取答案）+ DuReader_robust unanswerable 10 例（→ `unable`）。
   - 机制标签：`evidence_location`、`refuse_without_evidence`；顺带把 DuReader_robust 的 distracting 2~3 例打上 `fact_fidelity`。
   - 转换脚本放 `scripts/`（建议名 `build_v3_from_public.py`），逐例断言 quote 逐字命中 `sources[].text`；产出 `eval/datasets/research_writing_v3_public.json` 后跑 `python -m eval.research_cases` 校验。
   - 许可证义务：CC BY-SA 4.0（CMRC）——v3 文件内登记每例的 dataset/id 出处字段；若正文文本直接入库，v3 需按同许可证共享并注明（个人内部评测可，对外分发前再确认）。
2. **批次 B（冲突归属 + NEI→draft，约 15 例）**
   - CFEVER：同一维基实体的 SUPPORTS/REFUTES 成对 6 例（`conflict_attribution`）+ NEI 5 例（→`draft`，`conservative_grading`/`gap_declaration`）+ SUPPORTS 4 例对照。
   - 注意 quote 必须用证据句而非 claim（claim 为改写）。
3. **批次 C（语义支持率首个读数，评测端建设）**
   - 先在 ALCE 的 ASQA 子集（20 题）上跑通"报告引用 → 解析回来源 → 逐句 NLI"判分脚本；验证后把同一脚本应用到批次 A/B 的中文交付上（换中文 NLI 模型），产出项目首个**语义支持率读数**并记入 EXECUTION_STATUS，与 ≥95% 门槛对照。
   - 该读数在批次 A/B 真实跑通前不得写进任何验收结论。

执行与记录：三批合计约 45 例，按 `eval.business_eval --mode real` 单批成本口径先估预算再跑；结果进 `eval/reports/`，日期+批次+限制照例记 `docs/IMPLEMENTATION_LOG.md`，状态同步 `docs/EXECUTION_STATUS.md` 与 `docs/IMPLEMENTATION_TRACKER.md`。若某公开集质量不达标（quote 无法逐字对齐、材料过期腐化），只砍该集不换机制——缺口登记 `docs/OPTIMIZATION_BACKLOG.md`。

---

## 五、未核实清单与风险汇总

**许可证未明确/存疑（接入前必须确认）**

| 数据集 | 现状 |
|---|---|
| DRCD（[仓库](https://github.com/DRCKnowledgeTeam/DRCD)，30,000+ 中文 MRC，繁体） | license 字段 null |
| CHEF（[仓库](https://github.com/THU-BPM/CHEF)） | license 字段 null |
| SciFact（[仓库](https://github.com/allenai/scifact)） | license 字段 `Other`，README 无许可声明（社区常按 CC BY-NC 引用，**未核实**；AI2 数据页本次 403 无法打开） |
| MultiNews（[仓库](https://github.com/Alex-Fabbri/Multi-News)） | `NOASSERTION` |
| WikiAtomicEdits（[仓库](https://github.com/google-research-datasets/wiki-atomic-edits)） | license 字段 null，README 许可行疑指另一数据集 |
| TAT-DQA（[仓库](https://github.com/NExTplusplus/TAT-DQA)） | GitHub license 字段 null；项目页称 CC BY 4.0（快照） |
| Chinese-SimpleQA | CC BY-NC（论文脚注快照）——个人评测可用、禁商用、禁并入分发数据 |
| WCEP | 仓库 MIT，但 README 明言当前不提供完整数据下载 |
| GAIA / MP-DocVQA / ASQA（HF 页） | HF 本次限流无法打开数据页，许可证未核实 |

**其他风险**

- 二手数字：2Wiki 题量（19.2 万）、TAT-DQA 文档/QA 数、DuReader_robust 规模、Chinese-SimpleQA 题量、FreshQA 题量、MultiNews 规模等来自论文摘要或第三方转述，接入时以随包文件实际统计为准。
- 语言适配：除 CMRC 2018、DuReader_robust、CFEVER、Chinese-SimpleQA、WebCPM、TAT-DQA 外，第一梯队多为英文数据集；英文批次必须单独标 `lang` 并在报告中分组，不得混入中文合格率。
- 逐字性：凡"claim/answer 是改写或摘要式"的数据集（FEVER、CFEVER、CHEF、QASPER 摘要式答案），quote 一律回退到材料原文句，转换脚本必须强校验。
- 判分成本：SimpleQA/Chinese-SimpleQA/ALCE 需要模型裁判或 NLI 模型，先小样估算调用成本再放量。

## 参考链接索引（正文已散布，此处汇总常访入口）

- 本项目：`eval/datasets/research_writing_v1.json`、`docs/RESEARCH_WRITING_ACCEPTANCE.md`、`docs/IMPLEMENTATION_TRACKER.md`、`docs/OPTIMIZATION_BACKLOG.md`
- 中文数据：[CMRC 2018](https://hfl-rc.github.io/cmrc2018) · [DuReader_robust](https://github.com/PaddlePaddle/DuReader-robust) · [CFEVER](https://github.com/IKMLab/CFEVER-data) · [CHEF](https://github.com/THU-BPM/CHEF) · [WebCPM](https://github.com/thunlp/WebCPM) · [Chinese-SimpleQA](https://github.com/OpenStellarTeam/Chinese-SimpleQA) · [TAT-DQA](https://github.com/NExTplusplus/TAT-DQA)
- 英文数据：[QASPER](https://huggingface.co/datasets/allenai/qasper) · [FEVER](https://fever.ai/dataset/fever.html) · [HotpotQA](https://hotpotqa.github.io/) · [MuSiQue](https://github.com/StonyBrookNLP/musique) · [SQuAD 2.0](https://rajpurkar.github.io/SQuAD-explorer/) · [FreshQA](https://github.com/freshllms/freshqa) · [MultiNews](https://github.com/Alex-Fabbri/Multi-News) · [IteraTeR](https://github.com/vipulraheja/iterater) · [InjecAgent](https://github.com/uiuc-kang-lab/InjecAgent) · [HaluEval](https://github.com/RUCAIBox/HaluEval) · [ALCE](https://github.com/princeton-nlp/ALCE) · [RAGTruth](https://github.com/ParticleMedia/RAGTruth) · [GAIA](https://huggingface.co/datasets/gaia-benchmark/GAIA)
