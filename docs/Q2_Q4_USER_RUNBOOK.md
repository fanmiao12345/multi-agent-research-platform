# Q2～Q4 用户自测运行手册

本手册用于用户自行执行真实批次。所有真实模型/搜索命令都必须显式给出费用上限；脚本不会自动申请预算或代替人工评分。

## Q2-01 真实业务 99 次

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\q2_real.ps1 -MaxCost 0.25 -BatchMaxCost 6
```

- 33 个业务案例各 3 次，共 99 次；v1 20 例的 60 次在报告元数据中单列。
- 默认闭卷，不把关键事实/禁止断言注入写作端。
- `-MaxCost` 是**单任务**上限（美元估算），`-BatchMaxCost` 是**整批累计**上限（缺省 = 单任务上限 × 3）。达到整批上限或出现未知用量后，剩余尝试记 `not_executed`，不会静默继续花费（O-09）。
- 执行后生成 `eval/reports/q2_human_workbench/combined_scores.csv`。
- 人工填写正确性、结构、引用、完整性、改稿分钟后：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\q2_ingest.ps1
```

## Q2-02 真实联网与六模式

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\q2_web.ps1 -MaxCostPerTask 0.1
```

- 12 个公开主题；`single/fixed/manager_worker/fanout/dynamic_team/debate` 各至少 2 个主题。
- 真实 Bing 抓取、正文读取和引用谱系写入独立报告。
- 二层嵌套没有用模式名称冒充；沿用 D6/Q1 的受控嵌套证据。

## Q2-03 汇总

```powershell
.venv\Scripts\python -m eval.q2_summary --business eval/reports/q2_real_batch/business_report.json --web eval/reports/q2_web_baseline.json --human eval/reports/q2_real_batch/business_report_human.json
```

输出 `eval/reports/q2_summary.json/.md`。人工评分未确认时 `ready_for_q3=false`。

## Q3 对比优化

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\q3_compare.ps1 -Baseline eval/reports/q2_real_batch/business_report.json -Candidate eval/reports/q3_candidate/business_report.json
```

质量、费用、时延必须同时比较；无质量收益不得声明更优。优化项记录到 `docs/OPTIMIZATION_BACKLOG.md`。

## Q4 连续 7 天试用

每完成一个真实个人任务记录一次：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\q4_trial.ps1 -Command add -Task "真实任务" -Result "交付结果" -InterventionMinutes 5 -RevisionMinutes 10 -Notes "问题与限制"
```

查看是否达到 7 天、20 个任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\q4_trial.ps1 -Command status
```

## Q4 最终签收

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\q4_signoff.ps1
```

自动检查只生成草稿；`user_signoff` 始终保持 false，最终 1.0 必须由用户对照目标和整体标准签收。