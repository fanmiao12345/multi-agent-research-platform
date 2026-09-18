# 简历 ↔ 项目对照与补齐记录（RESUME PARITY）

版本：2026-09-16。目的：把简历《多Agent协作智能研究平台》逐条映射到代码落点与实测读数，
明确"已实现 / 本次补齐 / 仍无支撑"三类，供面试问答与后续补测使用。
读数一律来自 `eval/reports/resume_metrics/`（离线桩，可复现）与 Q2 真实批次报告。

## 一、逐条对照总表

| 简历条目 | 代码落点 | 状态 | 实测读数 / 诚实边界 |
|---|---|---|---|
| LangGraph 多Agent调度架构 | `src/graph/`、`src/harness/runtime/`（D-001 红线） | ✅ 原有 | 506→571 项离线回归全绿 |
| Fan-out / Pipeline / Delegation 编排 | `src/orchestration/`：pipeline / fanout / manager_worker / debate / dynamic_team / single 六模式 | ✅ 原有（比简历写的三种更多） | 六模式全部注册并接入统一入口；"Delegation"对应 manager_worker + `delegate_subagent` 受控工具 |
| 3 个 Subagent 并行 | fanout `max_parallel=3` 默认；嵌套深度≤2、派生≤12 | ✅ 原有 | Q2-01 六模式真实批次已覆盖 |
| **EventBus 解耦 Agent 间通信** | `src/orchestration/event_bus.py` + 六模式接线 + `tracer_bridge` | ✅ **R1 新增** | 线程安全/有序/订阅者异常隔离有测试；策略只发事件，Trace/UI 由订阅方实现 |
| **IterationBudget 预算控制与熔断** | `budget_control.IterationBudget`（预留/结算/耗尽/熔断 open→half_open）+ 接入 `delegate_subagent` | ✅ **R1 新增** | 连续失败 3 次熔断、拒绝继续派生有测试；原有 BudgetManager（次数/token/费用/时间）保留 |
| 平均耗时降低约 42.5% | `eval/resume_metrics.py` 口径 2 | ✅ 实测更高 | **66.5%**（6 子任务、并发 3、2.99x；100 轮均值±std 0.0008s/0.0004s）；**异质时长场景（0.5x~1.5x 随机抖动）59.4%（2.5x）**——结论对任务时长不均匀稳健。桩口径（机制收益），真实 LLM 时延对照待 Q3/Q4 批次 |
| **Skill Harness 生命周期** discovered→activated→running→deactivated | `src/harness/skills/lifecycle.py` 四态状态机 + Runtime 接线 | ✅ **R1 新增** | 非法迁移/异常不悬 running 有测试；热加载为原有 `SkillRegistry.reload()` |
| 依赖自动解析 | frontmatter `depends:` + 拓扑解析（缺失/循环显式报错） | ✅ **R1 新增** | alpha→beta→gamma 链式激活、循环拒绝有测试 |
| **三层渐进加载** metadata→fullcontent→references | `skills/types.py` 层级常量 + `registry.load_reference()` + `router.inject(level=...)` | ✅ **R1 新增**（references 层为新增） | **System Prompt token 节省率均值 81.6%**（17 次实验：每个技能轮流当命中技能；区间 78.7%~84.8%；路由阶段口径 87.4%）。简历 58% 偏保守，可改用实测口径 |
| 三层记忆 Working/Episodic/Semantic | `src/harness/memory/layers.py`：WorkingMemory（会话内有界）/ EpisodicLayer / SemanticLayer | ✅ **R1 新增门面** | 底层复用原有短期会话 + LongTermStore（semantic/episodic/procedural） |
| **SQLite + sqlite-vss 向量索引** | `src/harness/memory/vector_store.py`：SqliteVssIndex + HashingVectorIndex 零依赖回退，`build_index(auto)` | ✅ **R1 新增，已实装验证** | sqlite-vec 已安装，auto 真实切到 sqlite_vss 后端；修复 L2→余弦换算（cos=1−L2²/2），与零依赖实现双后端一致性测试通过（同 id 同分） |
| **记忆压缩与遗忘曲线衰减** | 压缩：原有 `summarize_text`/`trim_messages`；衰减：`retrievability=exp(-Δdays/stability)` + 召回强化 + `decay()` 清理 | ✅ **衰减 R1 新增** | 低于阈值 episodic 物理遗忘、semantic 只降权不删（有测试） |
| 跨会话知识复用命中率 78% | `eval/resume_metrics.py` 口径 3 | ✅ 实测同量级 | **top-3 74%**（2000 次 bootstrap，95% CI 65%~82%，±8.5）/ top-1 60%（CI 51%~69%）；100 条改述查询 × 100 条干扰知识、真实 sqlite_vss 后端 + 混合检索（向量余弦 + BM25/IDF 重排）口径 |
| **Provider 抽象 + 记忆注入 User Message 保持 Prompt Cache 前缀** | `src/harness/memory/provider.py`（MemoryProvider/LayeredMemoryProvider/inject_into_user）+ `compose_context(user_message_kinds=...)` + `RuntimeContext.memory_in_user_message` | ✅ **R1 新增机制** | `<<CONTEXT>>` 标记保留（网关兼容）；默认 False 保持 Q2 基线可比，开启后 system 前缀逐字节稳定（有测试）；默认值切换属配置决策 |
| 全链路评测：量化指标 / Trajectory / LLM-as-a-Judge / A-B 模型对照 | `eval/evaluators/*`、`tracer`（run.json/trace.jsonl）、`eval/grader.py`（独立评测者+人工双轨）、`eval/benchmark_model.py` | ✅ 原有 | Q2-01：99 次闭卷真实批次、独立评测 99/99 打分、人工评分表 ingest（human_confirmed=true） |
| **漂移检测与阈值告警** | `eval/drift.py`：基线对比/绝对+相对阈值/逐指标覆盖/丢失指标告警/CLI 退出码 0/1 | ✅ **R1 新增** | 真实报告自检 exit=0；可挂 q2_summary / q3_compare 之后做自动体检 |
| **漂移趋势（滑动均值）** | `drift.detect_trend()`：滑动均值 z 分数离群（突发退化）+ 连续 N 批单边漂移（缓慢劣化）双通道，CLI `--history` | ✅ **R1 追加补齐** | 稳定序列不误报、尖峰/缓降都能抓住（各有测试） |
| **A/B 实验 + Welch's t 检验** | `eval/experiment.py`：ExperimentRunner 控制变量对照 + 零依赖 Welch 检验（p 值用正则化不完全 Beta 连分式，不引 scipy） | ✅ **R1 追加补齐** | p 值对教科书参考值校验（t=2,df=10→0.0734）；显著性 + better 变体判定 |
| **AutoOptimizer 参数反馈闭环** | `eval/auto_optimizer.py`：指标阈值规则→白名单参数（context_budget/max_output_tokens/max_calls）受控调整；CLI `python -m eval.auto_optimizer --metrics <报告>` | ✅ **R1 追加补齐（护栏版），已用真实数据演示** | 对 Q2 真实汇总跑 dry-run：p95=369.4s 正确触发时延护栏（8192→7782.4，未落盘）、accepted 占比 0.81 正确不触发质量规则；结果存 `eval/reports/auto_optimizer/demo_q2_dry_run.json`。三道护栏：白名单+硬边界夹取+默认 dry_run 审计日志；**是否采纳建议属 Q3 决策，未自动应用** |
| **FastAPI + React** | `src/interfaces/web/fastapi_app.py`（同契约适配层 + `/vendor` 静态路由）+ `static/react/index.html`（React 18 本地 vendor，CDN 兜底） | ✅ **R1 新增，浏览器级已验收** | Playwright 1.63 + Chromium 153 真浏览器验收通过（渲染/逐键输入/提交/轮询到终态/截图/无控制台错误 + 离线回退提示），证据 `eval/reports/react_browser/`；验收揪出并修复 2 个真实缺陷（vendor 静态路由缺失、useState 解构错误）；HTTP 级 5 项测试真跑；默认入口仍是零依赖标准库工作台 |
| **Prompt Cache 前缀不变** | `provider.py` + `compose_context(user_message_kinds)` + 开关 `memory_in_user_message` | ✅ 机制成立 / ⚠️ **收益待 O-15 切换（已有对照读数）** | 实测（口径 4）：system 前缀逐字节稳定 ✅；当前关闭态两种注入位置增益 ≈0（O-15 历史窗口恒 2 条压制）；**O-15 开关开启后增益 +28.9pp**（user 71.3% vs system 42.4%）——结构性优势被证实，切换待 Q3 批次收尾；**简历措辞：保留"前缀不变"（真），缓存收益提法等 O-15 切换后重测再说** |
| 研究任务端到端完成率 85% | Q2-01 真实批次 | ❌ **无此读数** | 真实读数三分离：执行完成率 **97.0%**（96/99）、预期行为符合率 **70.8%**（90% 门槛未达标）、成品质量 **61.3%**（独立评测）/ **71.3%**（人工）；简历口径建议改用其一，勿写 85% |

## 二、实测复现命令

```powershell
# 三个量化读数（全离线；100 轮计时 + 2000 次 bootstrap + 17 技能全枚举）
.venv\Scripts\python -m eval.resume_metrics --sleep 0.15 --repeats 100
# 报告落 eval/reports/resume_metrics/resume_metrics.{json,md}

# 漂移检测（对比两份指标 JSON；ok=0 / drift=1）
.venv\Scripts\python -m eval.drift --baseline <基线.json> --current <当前.json> --abs 0.05 --rel 0.20

# FastAPI 适配层（可选依赖：pip install fastapi uvicorn）
.venv\Scripts\python -m src.interfaces.web.fastapi_app --port 8766
```

## 三、面试追问对照（口径怎么说）

- "并行为什么快 66%？"：6 个独立子任务、3 并发线程池（Amdahl：加速比≈min(并发, 子任务数)=3，串行汇总开销小）；20 轮计时均值±std 0.0007s/0.0003s，中位数口径同为 66.5%。
- "token 省 81.6% 怎么算的？"：对照"全部技能全文注入"（5819 tokens）与"metadata 召回 + 命中 1 技能 fullcontent"；17 个技能逐个当命中技能各测一次取均值，区间 78.7%~84.8%（最差情况也省近八成）。
- "命中率 74% 可信吗？"：100 条跨会话改述查询 × 100 条干扰知识（最难口径），top-3 点估计 74%，2000 次 bootstrap 95% CI 65%~82%——**区间 ±8.5 个点**；检索是混合口径（向量余弦 + BM25/IDF 重排，IDF 压低"任务/预算"这类高频域词），跑在真实 sqlite-vec 后端上。演进如实讲：10 例时 80% 但 CI 宽达 ±25 → 扩到 100 例后点估计回落到 74%、区间收窄到 ±8.5——先扩样本再下结论。
- "sqlite-vss 用了吗？"：适配层就绪、接口与后端无关；当前环境无扩展自动回退哈希余弦并如实标注 backend——**别说已经在生产用 sqlite-vss**。
- "85% 完成率？"：项目口径是三分离读数（97% / 70.8% / 61.3~71.3%），没有 85% 这个数；建议简历改写。
- "EventBus 是不是过度设计？"：六模式策略原本各自直连日志/UI；总线后策略只 publish，Trace 桥/进度订阅方各自挂载，测试覆盖线程安全与订阅者隔离。

## 五、评测体系名称映射（面试被问"类名怎么对不上"时用）

| 简历/描述里的名字 | 项目实际落点 | 说明 |
|---|---|---|
| MetricsCollector | `UsageTracker`（token/调用数）+ `eval/evaluators/*_metrics`（工具/上下文/技能/记忆）+ `business_eval`（时延/费用），每次 run 自动落 usage.json/context.json | 功能等价，采集分散在各 run 产物里 |
| TrajectoryRecorder | `harness/tracer.py`（run.json/trace.jsonl）+ `evaluators/trajectory.py` | 完整推理链回溯 |
| LLMEvaluator | `eval/grader.py`（独立评测者，GRADER_MODEL_NAME 可换模型并记录独立性局限）+ `human_scores.py` 人工双轨 | 四维 = 正确性/结构/引用/完整性（1~5 分）；"效率"走时延/费用指标不占评分维度 |
| DriftDetector | `eval/drift.py`：`check_drift`（基线阈值）+ `detect_trend`（滑动均值/持续单边漂移） | 两通道齐全 |
| ExperimentRunner | `eval/experiment.py`（Welch's t 检验零依赖实现） | 控制变量 + 统计显著性 |
| AutoOptimizer | `eval/auto_optimizer.py`（护栏版闭环） | 白名单参数/硬边界/dry_run 默认/审计日志 |

## 六、诚实边界（不会在面试里翻车的前提）

1. **85% 完成率无出处**——要么改用真实三分离读数，要么申请预算跑新批次（Q4-01 前）。
2. sqlite-vss / FastAPI / React 均为**可选层**：依赖已实装、测试真跑（605 项 0 失败 0 跳过，含 2 项真实 Chromium 浏览器验收），但默认运行入口仍是零依赖标准库工作台 + 哈希余弦/vss 自动选择。
3. `memory_in_user_message` 默认关闭（保持 Q2 基线可比）；**实测收益当前 ≈0，受 O-15（历史窗口恒 2 条）压制**，修复 O-15 前简历只说"前缀不变"不说"命中率提升"。
4. 漂移检测阈值（abs 0.05 / rel 0.20）与 AutoOptimizer 规则阈值（截断 20%/p95 400s/预算停止 10%）是实施默认值，未经 Q3 数据校准；AutoOptimizer 的建议**未自动应用**（dry-run 演示）。
5. 跨会话命中率基于 100 条自建改述用例（sqlite_vss + BM25/IDF 混合重排口径）；换真实 embedding 模型需重测；简历的 78% 与实测 top-3 74% 同量级，建议简历改用 74%（或写"约七成半"）。
6. 提速读数基于固定/抖动耗时桩（100 轮均值，机制收益）；真实 LLM 调用方差大、且子任务非完全独立时低于该值，真实对照待 Q3/Q4 批次。
7. React 页浏览器验收覆盖 FastAPI 演示适配层；标准库工作台的浏览器清单按原计划留在 7 天试用期人工执行。
