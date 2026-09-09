# 最终实验报告（Final Report）
- 生成：2026-09-09T13:06:37｜大脑：mock-rule-v1（全离线，无 token 消耗）

## 1. 组件指标
- Agent Benchmark：executed 5，成功率 100.0%，Tool Selection 100.0%，Tool Argument 100.0%
- Skill Eval（recall@1）：整体 100.0%（positive 100.0% / negative 100.0%）

## 2. Orchestration 策略对比（同一任务集）
| 策略 | 成功 | 平均 worker 调用 | 平均产出长度 |
|---|---|---|---|
| debate | 2/2 | 2.0 | 252 |
| dynamic_team | 2/2 | 3.0 | 291 |
| fanout | 2/2 | 3.0 | 256 |
| manager_worker | 2/2 | 3.0 | 272 |
| pipeline | 2/2 | 4.0 | 71 |
| single | 2/2 | 1.0 | 71 |

## 3. Model/Budget 三档策略（I 阶段验收）
| mode | profile | iterations | final_len |
|---|---|---|---|
| low_budget | cheap | 2 | 71 |
| balanced | balanced | 2 | 71 |
| high_quality | balanced | 2 | 71 |

## 4. Ablation 问答（文档第 16 节）
- **No Planning vs Planning**：Planning 侧落地（M3）：5 步任务可自动成图并差异式重规划救回失败（实验：T3 失败→replan→3/3 完成）。Mock 下质量差异需真实模型补跑。
- **No Memory vs Memory**：Memory 侧落地（M6）：跨调用 Checkpointer + Long-term + Knowledge 检索；Eval：precision/recall=100%、注入率 0。
- **Full Context vs Context Engine**：Context 侧落地（M5）：token 节省 >50% 且尾部信息保留（context_metrics 实测）。
- **Single vs Multi-Agent**：见上表：single=1 次 worker 调用/产出 71 字；pipeline=4 次；manager/fanout/dynamic≈3 次且产出更长（256-291 字）——Mock 语义下 Multi 用更多调用换更完整结构，真实质量对比待真实模型。
- **Sequential vs Fan-out**：fanout 与 dynamic 并行批次执行（3 子题并发），与 pipeline 串行对比：调用数相近、产出更长。
- **No Reviewer vs Reviewer**：pipeline 四棒含 reviewer 阶段（产出可被 reviewer 校验）；Manager/Dynamic 用 Replanner 充当 Reviewer 反馈闭环。
- **Fixed Model vs Model Routing**：Model/Budget 三档实测路由正确（工具任务永不落到 no_tools 的 deep），低预算档固定 cheap。
- **No Recovery vs Durable Execution**：故障实验：进程中断后 resume 只重跑未完成任务（executed [T1,T2]→[T2,T3]），幂等 ledger 保证副作用不重放。
- **成本/延迟结论**：Mock 大脑无真实 token，成本/延迟列待配置 .env 后补跑（运行摘要行与 usage.json 已就绪，读数表已备好）。
