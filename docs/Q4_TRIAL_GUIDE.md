# Q4 七天个人试用——操作指南（给用户的速查手册）

版本：2026-09-20。目标：连续 7 天、≥20 个**真实工作任务**，全程真实模式，
记录额外干预/改稿时间/未完成原因 → 驱动 Q4-02 集中修复 → Q4-03 最终签收。
配套：`docs/TRIAL_LOG_TEMPLATE.md`（日志表）、`scripts/q4_trial.ps1`（记录助手）、
`docs/Q2_Q4_USER_RUNBOOK.md`（命令总册）。

## 0. 开工前一次性检查（第 1 天开始前做一遍）

```powershell
# ① 配置就绪（应显示 model/search 已配置；不要把 Key 发给任何人）
.venv\Scripts\python -m src.ops.health
# ② 备份当前工作区（可选但推荐）
.venv\Scripts\python -m src.ops.backup
```

- `.env` 已配置（MODEL_API_KEY / SEARCH_PROVIDER=bing_scrape）✅
- 全量回归 617 项 0 失败 ✅（Q3-03 已验证）

## 1. 每天的操作流程

### ① 启动工作台（推荐方式，浏览器操作）

```powershell
.venv\Scripts\python -m src.interfaces.web.workbench --port 8765
```

浏览器打开 `http://127.0.0.1:8765`。首页输入目标 → 选模式 **real** → 提交。
任务卡会显示：阶段进度 / 交付等级（accepted·draft·unable）/ 待输入 / 审批 / 停止·恢复。

### ② 或者用 CLI（适合脚本习惯）

```powershell
# 研究写作类（允许联网搜索）
.venv\Scripts\python -m src.interfaces.cli "把这三份周报整理成一份月度综述" ^
  --mode real --flow research --allow-network --import-file 周报1.txt --import-file 周报2.txt

# 简单问答/计算/单点处理
.venv\Scripts\python -m src.interfaces.cli "27*43 等于多少" --mode real

# 基于原稿改稿
.venv\Scripts\python -m src.interfaces.cli "把报告压缩到180字以内" --mode real --revise-job <上次job_id>
```

不加 `--max-cost/--max-seconds` 也可以：出厂默认已落定（$0.15 / 600s）。

### ③ 什么算"合格的真实任务"

- **真实工作**：整理材料、写综述/报告、比较分析、依据材料核查说法、改稿——你真的需要结果的任务；
- **真实模式**：`--mode real`（mock 跑的不计入）；
- **三类都可以**：资料整理 / 研究写作 / 改稿，另有简单问答（计入但占比别太高）；
- **不算**：eval 批次、故意捣乱的测试输入、纯闲聊。

### ④ 结果怎么用与检查

- 任务卡点开报告：**引用 [E-xxx] 可点击定位原文**——顺手验证有没有编造来源；
- `draft` ≠ 失败：可直接在任务卡**追问改稿**（同一会话新版本，旧稿保留）；
- `unable` 会说明缺什么——这本身就是有价值的数据（记入日志）；
- 导出：任务卡提供 Markdown/HTML 导出。

### ⑤ 每完成一个任务，记一条日志

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\q4_trial.ps1 -Command add ^
  -Task "整理3份周报材料" -Result "accepted，引用可定位" ^
  -InterventionMinutes 5 -RevisionMinutes 10 -Notes "第2份日期字段缺失已如实标注"
```

- `-InterventionMinutes`：你额外干预的分钟数（改提示词、补资料、点审批等）；
- `-RevisionMinutes`：你对产出做人工修改的分钟数；
- `-Notes`：问题/异常/亮点，**脱敏**（不要贴真实敏感内容）。
- 随时查看进度：`-Command status`（自动统计 7 天 / 20 任务是否达标）。

### ⑥ 每天结束 1 分钟自查（来自 TRIAL_LOG_TEMPLATE）

- [ ] 今天至少 1 条记录；断网/取消/重启等故障是否遇到过（遇到了就记）；
- [ ] 报告引用能否定位原文；有没有编造来源；
- [ ] draft/unable 的原因说明是否看得懂（不是只有异常类型）；
- [ ] 新问题顺手写进"问题登记"表（结束后我统一转成可复现用例，Q4-02 修复）。

## 2. 常见状态处理

| 状态 | 含义 | 你要做的 |
|---|---|---|
| waiting_input | 缺关键条件（如没说比较对象） | 在任务卡补充信息即可继续，不用重跑 |
| 待审批 | 高风险工具请求 | 任务卡批准/拒绝（记录进 Notes） |
| draft | 有成果但有缺口 | 追问改稿，或接受并记录 |
| unable | 诚实拒绝（缺资料/超能力） | 记录原因——这是加分数据不是事故 |
| 中断/重启 | 服务重启后任务不丢 | 任务卡"恢复"；CLI `--resume-job <id>` |
| 取消 | 阶段边界收敛，产物保留 | 已完成部分可查看/导出 |

## 3. 结束（第 7 天）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\q4_trial.ps1 -Command status   # 确认 7 天/20+
powershell -ExecutionPolicy Bypass -File .\scripts\q4_signoff.ps1                 # 生成签收草稿
```

把试用日志/问题清单交给我 → 我执行 **Q4-02**（集中修复 + 复验）→ **Q4-03**（最终签收，
`user_signoff` 由你本人确认为 true 才算 1.0）。

## 4. 试用规则（红线）

1. **不把 eval 批次算作试用**——eval 是我跑的，试用是你真用的；
2. **不把敏感真实资料提交进仓库**（workspaces/ 已忽略，日志里脱敏）；
3. 试用期间发现问题**只记录不修代码**（修复统一走 Q4-02，避免污染观察）；
4. 费用预期：约 $0.05/任务，20+ 任务 ≈ **$1~1.5**；默认兜底 $0.15/任务已生效；
5. 中途想停就停：`status` 会如实记录中断，断点可恢复。
