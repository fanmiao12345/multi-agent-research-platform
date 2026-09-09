# Benchmark Report：benchmark_v1
- 生成时间：2026-09-09T09:37:31
- 模式：mock（运行器冒烟测试，不代表研究写作业务验收）
- 数据集任务数：13｜大脑：mock-rule-v1

## 总体（executed=5）
- ✅ 通过 5 ｜ ❌ 失败 0 ｜ ⏭ 跳过 8
- 成功率：100.0%
- Tool Selection 准确率：100.0%
- Tool Argument 准确率：100.0%
- 平均 LLM 调用次数：1.6
- vs 上一版成功率：0.0%

## 按类别
| 类别 | 执行 | 通过 |
|---|---|---|
| simple_qa | 1 | 1 |
| single_tool | 2 | 2 |
| long_text | 1 | 1 |
| tool_failure | 1 | 1 |

## 逐任务
| id | 类别 | 状态 | 终止原因 | 工具 | 重复峰值 |
|---|---|---|---|---|---|
| b01 | single_tool | ✅ | success | calculator | 1 |
| b02 | single_tool | ✅ | success | current_time | 1 |
| b03 | wrong_tool_bait | ⏭ outside_smoke_subset | - | - | - |
| b04 | multi_tool | ⏭ outside_smoke_subset | - | - | - |
| b05 | simple_qa | ✅ | success | — | 0 |
| b06 | long_text | ✅ | success | — | 0 |
| b07 | multi_step | ⏭ outside_smoke_subset | - | - | - |
| b08 | search_task | ⏭ capability_missing | - | - | - |
| b09 | parallel_task | ⏭ outside_smoke_subset | - | - | - |
| b10 | conflicting_evidence | ⏭ outside_smoke_subset | - | - | - |
| b11 | tool_failure | ✅ | success | calculator | 1 |
| b12 | needs_human | ⏭ outside_smoke_subset | - | - | - |
| b13 | planning_needed | ⏭ capability_missing | - | - | - |
