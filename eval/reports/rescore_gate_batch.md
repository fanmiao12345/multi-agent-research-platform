# 口径重算（S8-A：三指标分离）

| 指标 | 数值 |
|---|---|
| 执行完成率 | 20/20 = 100.0% |
| 预期行为符合率 | 12/20 = 60.0%（partial 单列：无） |
| 成品质量达标率（链内 accepted 中独立评测 accept） | 7/14 = 50.0% |
| 引用可定位率（链内 accepted） | 100.0% |
| 链内 accepted 四维均值（正确性/结构/引用/完整性） | 3.93 / 4.86 / 3.93 / 4.79 |
| 链内 accepted 带伪造/无依据标记 | 3 例 / 7 条 |
| 链内 accepted 禁语命中 | 0 次 |

## 诚实口径

- 独立评测 Agent 的评分 human_confirmed=false，属初步自动评分；业务通过率以人工确认为准。
- 开卷提示效应：旧批次把数据集关键事实作为任务要求传入写作链，事实命中率的提升含提示效应；本重算只改统计口径，不能消除旧运行已获得参考答案的影响。
- 本工具只读历史报告，不修改、不重跑、不合并任何产物；新旧口径数字不可直接相加或平均。

## 分机制比例（一例可属多机制，分母=该机制案例数）

| 机制 | 案例 | 执行 | 预期符合 | 链内accepted | 独立accept | 质量accept率 |
|---|---|---|---|---|---|---|
| conflict_attribution | 2 | 2 | 2 | 2 | 1 | 50.0% |
| conservative_grading | 2 | 2 | 0 | 2 | 1 | 50.0% |
| dedup | 2 | 2 | 0 | 0 | 0 | — |
| derived_calculation | 3 | 3 | 2 | 2 | 0 | 0.0% |
| evidence_location | 2 | 2 | 2 | 2 | 1 | 50.0% |
| evidence_strength | 1 | 1 | 0 | 0 | 0 | — |
| fact_fidelity | 2 | 2 | 1 | 1 | 1 | 100.0% |
| gap_declaration | 3 | 3 | 2 | 3 | 0 | 0.0% |
| instruction_isolation | 1 | 1 | 0 | 0 | 0 | — |
| refuse_without_evidence | 1 | 1 | 0 | 1 | 1 | 100.0% |
| revision_compression | 1 | 1 | 1 | 1 | 1 | 100.0% |
| timeliness | 2 | 2 | 2 | 2 | 1 | 50.0% |
| withdrawn_source | 1 | 1 | 1 | 1 | 1 | 100.0% |

## 分批次（batch）

| batch | 案例 | 执行 | 预期符合 | 链内accepted | 独立accept | 质量accept率 |
|---|---|---|---|---|---|---|
| v1 | 20 | 20 | 12 | 14 | 7 | 50.0% |

## 逐报告

| 报告 | 完成 | 符合 | 链内accepted→独立accept |
|---|---|---|---|
| eval/reports\gate_real_o01\business_report.json | 1/1 | 1/1 | 0/1 |
| eval/reports\gate_real_o02\business_report.json | 1/1 | 1/1 | 1/1 |
| eval/reports\gate_real_o03\business_report.json | 1/1 | 0/1 | 0/0 |
| eval/reports\gate_real_o04\business_report.json | 1/1 | 1/1 | 0/1 |
| eval/reports\gate_real_o05\business_report.json | 1/1 | 1/1 | 0/1 |
| eval/reports\gate_real_o06\business_report.json | 1/1 | 1/1 | 1/1 |
| eval/reports\gate_real_o07\business_report.json | 1/1 | 1/1 | 0/1 |
| eval/reports\gate_real_o08\business_report.json | 1/1 | 0/1 | 0/0 |
| eval/reports\gate_real_r01\business_report.json | 1/1 | 0/1 | 0/0 |
| eval/reports\gate_real_r02\business_report.json | 1/1 | 0/1 | 0/0 |
| eval/reports\gate_real_r03\business_report.json | 1/1 | 0/1 | 0/1 |
| eval/reports\gate_real_r04\business_report.json | 1/1 | 1/1 | 0/1 |
| eval/reports\gate_real_r05\business_report.json | 1/1 | 1/1 | 1/1 |
| eval/reports\gate_real_r06\business_report.json | 1/1 | 1/1 | 0/1 |
| eval/reports\gate_real_r07\business_report.json | 1/1 | 0/1 | 1/1 |
| eval/reports\gate_real_r08\business_report.json | 1/1 | 0/1 | 0/0 |
| eval/reports\gate_real_v01\business_report.json | 1/1 | 1/1 | 1/1 |
| eval/reports\gate_real_v02\business_report.json | 1/1 | 1/1 | 1/1 |
| eval/reports\gate_real_v03\business_report.json | 1/1 | 0/1 | 0/0 |
| eval/reports\gate_real_v04\business_report.json | 1/1 | 1/1 | 1/1 |

## 逐案例

| id | 预期 | 交付 | 一致 | 独立评测 | 引用(未解析/总数) | 伪造标记 | 禁语 |
|---|---|---|---|---|---|---|---|
| o01 | 成品 | 成品 | √ | draft | 0/0 | 3 | 0 |
| o02 | 成品 | 成品 | √ | accept | 0/0 | 0 | 0 |
| o03 | 成品 | 草稿 | × | fail | 0/0 | 2 | 0 |
| o04 | 成品 | 成品 | √ | draft | 0/36 | 0 | 0 |
| o05 | 成品 | 成品 | √ | draft | 0/0 | 0 | 0 |
| o06 | 成品 | 成品 | √ | accept | 0/0 | 0 | 0 |
| o07 | 成品 | 成品 | √ | draft | 0/0 | 3 | 0 |
| o08 | 成品 | 草稿 | × | fail | 0/0 | 3 | 0 |
| r01 | 成品 | 草稿 | × | draft | 0/0 | 0 | 0 |
| r02 | 成品 | 草稿 | × | accept | 0/0 | 0 | 0 |
| r03 | 草稿 | 成品 | × | draft | 0/41 | 0 | 0 |
| r04 | 成品 | 成品 | √ | draft | 0/42 | 1 | 0 |
| r05 | 成品 | 成品 | √ | accept | 0/0 | 0 | 0 |
| r06 | 成品 | 成品 | √ | draft | 0/105 | 0 | 0 |
| r07 | 无法完成 | 成品 | × | accept | 0/0 | 0 | 0 |
| r08 | 成品 | 草稿 | × | accept | 0/49 | 0 | 0 |
| v01 | 成品 | 成品 | √ | accept | 0/9 | 0 | 0 |
| v02 | 成品 | 成品 | √ | accept | 0/0 | 0 | 0 |
| v03 | 成品 | 草稿 | × | accept | 0/38 | 0 | 0 |
| v04 | 成品 | 成品 | √ | accept | 0/22 | 0 | 0 |
