# Q2-01 人工打分说明（99 行）

## 怎么填
1. 打开 `q2_human_workbench/combined_scores.csv`（99 行，一行一次尝试）。
2. 只填每行的 `correctness` / `structure` / `citations` / `completeness`（1~5）与 `revised_minutes`（你实际改稿分钟）；其余列请勿改动。
3. 报告全文在 `report_texts/<案例>_rep<第几次>.md`；下面的表格告诉你每行对应哪份文本。
4. 打分口径：1=不可用 2=大量错误 3=可用但有明显不足 4=满足要求 5=可直接交付；报告要算达标需四项均≥4。

## 没有报告文本的行（没有可读报告，建议留空或按交付失败处理）
r02#3、r03#2、r05#2、v04#3、v06#3

## 参考材料（可选）
- `reference_verdicts.csv`：独立评测（v4-pro）的裁决与四维分数，**仅供参考**，可完全不看。
- 表内已有的 `machine_*` 列是程序检查（章节/事实/引用/禁语），同样仅供参考。

## 填完之后
告诉我“填好了”，我执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\q2_ingest.ps1
```

它会写入 `eval/reports/q2_real_batch/business_report_human.json`（human_confirmed=true），然后我用 `eval.q2_summary` 出 Q2-03 汇总；人工确认是 Q2 的权威口径，未确认前 `ready_for_q3=false`。

## 时间参考
99 行、约 1.5~2 小时；可分批填，未填的行不会写入确认结果。
