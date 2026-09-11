# 口径重算（S8-A：三指标分离）

| 指标 | 数值 |
|---|---|
| 执行完成率 | 13/13 = 100.0% |
| 预期行为符合率 | 10/13 = 76.9%（partial 单列：无） |
| 成品质量达标率（链内 accepted 中独立评测 accept） | 9/13 = 69.2% |
| 引用可定位率（链内 accepted） | 100.0% |
| 链内 accepted 四维均值（正确性/结构/引用/完整性） | 4.15 / 4.85 / 4.08 / 4.15 |
| 链内 accepted 带伪造/无依据标记 | 4 例 / 6 条 |
| 链内 accepted 禁语命中 | 2 次 |

**人工确认口径：未导入（读数仅为独立评测初步分，human_confirmed=false）**

## 诚实口径

- 独立评测 Agent 的评分 human_confirmed=false，属初步自动评分；业务通过率以人工确认为准。
- 开卷提示效应：旧批次把数据集关键事实作为任务要求传入写作链，事实命中率的提升含提示效应；本重算只改统计口径，不能消除旧运行已获得参考答案的影响。
- 本工具只读历史报告，不修改、不重跑、不合并任何产物；新旧口径数字不可直接相加或平均。

## 分机制比例（一例可属多机制，分母=该机制案例数）

| 机制 | 案例 | 执行 | 预期符合 | 链内accepted | 独立accept | 质量accept率 | 
|---|---|---|---|---|---|---|
| conflict_attribution | 1 | 1 | 1 | 1 | 0 | 0.0% |
| conservative_grading | 3 | 3 | 0 | 3 | 2 | 66.7% |
| controversy_balance | 2 | 2 | 2 | 2 | 1 | 50.0% |
| dedup | 1 | 1 | 1 | 1 | 0 | 0.0% |
| evidence_location | 5 | 5 | 5 | 5 | 4 | 80.0% |
| evidence_strength | 2 | 2 | 2 | 2 | 1 | 50.0% |
| gap_declaration | 3 | 3 | 1 | 3 | 1 | 33.3% |
| instruction_isolation | 1 | 1 | 1 | 1 | 1 | 100.0% |
| refuse_without_evidence | 1 | 1 | 0 | 1 | 1 | 100.0% |
| revision_add_perspective | 1 | 1 | 1 | 1 | 1 | 100.0% |
| revision_cleanup | 1 | 1 | 1 | 1 | 1 | 100.0% |
| revision_no_change_guard | 1 | 1 | 1 | 1 | 1 | 100.0% |
| subtopic_decomposition | 2 | 2 | 1 | 2 | 1 | 50.0% |
| timeliness | 1 | 1 | 1 | 1 | 1 | 100.0% |
| withdrawn_source | 1 | 1 | 1 | 1 | 1 | 100.0% |

## 分批次（batch）

| batch | 案例 | 执行 | 预期符合 | 链内accepted | 独立accept | 质量accept率 |
|---|---|---|---|---|---|---|
| v2 | 13 | 13 | 10 | 13 | 9 | 69.2% |

## 逐报告

| 报告 | 完成 | 符合 | 链内accepted→独立accept |
|---|---|---|---|
| eval/reports\closed_v2_o09\business_report.json | 1/1 | 1/1 | 0/1 |
| eval/reports\closed_v2_o10\business_report.json | 1/1 | 1/1 | 0/1 |
| eval/reports\closed_v2_o11\business_report.json | 1/1 | 1/1 | 1/1 |
| eval/reports\closed_v2_o12\business_report.json | 1/1 | 1/1 | 1/1 |
| eval/reports\closed_v2_o13\business_report.json | 1/1 | 0/1 | 1/1 |
| eval/reports\closed_v2_r09\business_report.json | 1/1 | 1/1 | 1/1 |
| eval/reports\closed_v2_r10\business_report.json | 1/1 | 1/1 | 0/1 |
| eval/reports\closed_v2_r11\business_report.json | 1/1 | 0/1 | 0/1 |
| eval/reports\closed_v2_r12\business_report.json | 1/1 | 0/1 | 1/1 |
| eval/reports\closed_v2_r13\business_report.json | 1/1 | 1/1 | 1/1 |
| eval/reports\closed_v2_v05\business_report.json | 1/1 | 1/1 | 1/1 |
| eval/reports\closed_v2_v06\business_report.json | 1/1 | 1/1 | 1/1 |
| eval/reports\closed_v2_v07\business_report.json | 1/1 | 1/1 | 1/1 |

## 逐案例

| id | 预期 | 交付 | 一致 | 独立评测 | 引用(未解析/总数) | 伪造标记 | 禁语 |
|---|---|---|---|---|---|---|---|
| o09 | 成品 | 成品 | √ | draft | 0/0 | 1 | 0 |
| o10 | 成品 | 成品 | √ | fail | 0/0 | 2 | 0 |
| o11 | 成品 | 成品 | √ | accept | 0/0 | 0 | 0 |
| o12 | 成品 | 成品 | √ | accept | 0/19 | 0 | 2 |
| o13 | 草稿 | 成品 | × | accept | 0/0 | 0 | 0 |
| r09 | 成品 | 成品 | √ | accept | 0/0 | 0 | 0 |
| r10 | 成品 | 成品 | √ | fail | 0/31 | 1 | 0 |
| r11 | 草稿 | 成品 | × | draft | 0/60 | 2 | 0 |
| r12 | 无法完成 | 成品 | × | accept | 0/0 | 0 | 0 |
| r13 | 成品 | 成品 | √ | accept | 0/0 | 0 | 0 |
| v05 | 成品 | 成品 | √ | accept | 0/51 | 0 | 0 |
| v06 | 成品 | 成品 | √ | accept | 0/0 | 0 | 0 |
| v07 | 成品 | 成品 | √ | accept | 0/13 | 0 | 0 |
