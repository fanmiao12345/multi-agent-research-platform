# B5交付记录：研究写作链（证据→素材→提纲→初稿→双层审校→有限修订）

日期：2026-09-09。已验收范围：离线代码与桩模型全链行为；真实模型业务质量、
20个业务案例执行、浏览器人工验收未执行（S6）。

## 交付

1. **固定阶段链（S3-01）**：`src/application/pipeline/`。顺序固定：evidence → material →
   outline → draft → review（可带≤2轮修订）。执行架构不由 LLM 生成。
2. **契约与证据（S3-04）**：evidence.json 权威存储；证据编号稳定（E-001…）；每条证据含
   source_id/事实/标注(F/I/U)/**逐字摘录**/字符偏移/**段落定位**（与来源分段同一口径）。
   程序先校验 quote 逐字存在于全文并算出定位，未定位即丢弃并记 error issue——不进证据库。
3. **素材包（S3-05）**：按主题组织事实与证据；**冲突 status 强制 open**（模型试图"解决"
   矛盾会被拒绝并记 issue，无依据不裁决）；缺口如实列出；转载/重复来源由程序从来源登记补齐。
4. **提纲与初稿（S3-06）**：提纲 required_evidence 只保留真实证据 id；正文使用 [E-xxx] 引用
   标记，要求节带〔事实/推断/未知〕标注；章节标题与提纲一致（程序层复验）。
5. **双层审校（S3-09/10）**：程序层检查引用可解析、必需证据全覆盖、章节齐全、标注存在；
   模型层检查 support/missing/conflict/style；模型判 accepted 但含 error 自动改判。
   **最多修订 2 轮**：每轮问题清单落 review.vN、新稿落 report.v(N+1)，旧稿永不覆盖。
6. **结束≠成功（S3-11/12）**：结果分级 accepted/draft/failed——修订轮次耗尽、预算停止
   （BudgetStop 收敛）、证据/资料缺口都交付"待完善草稿"并保留全文，不显示验收成功；
   阶段解析失败记 failed。阶段产物全部版本化落 artifacts，pipeline.json 记录阶段快照。
7. **统一入口**：`TaskRequest.flow`（agent|research）；ResearchApplication 在同根账本内跑链
   （全部模型调用过 model_call，purpose=evidence_extract/material_pack/outline/draft/review，
   预算限制天然生效）；job.json 记录 pipeline 摘要；CLI `--flow research` 端到端可用；
   Web 明确提示链入口随 S5 批次（页面只读 artifacts 已可用）。
8. **上下文预算（S3-07 雏形）**：单来源证据调用 40k 字符预算、素材/提纲 20k 上限，
   截断显式标注禁止猜测后段；证据索引始终带 id+摘录，摘要不作唯一业务输入。

## 使用

```powershell
# 整理并写报告（离线验收用桩/真实模型均可；真实模式需配置 .env）
.venv\Scripts\python -m src.interfaces.cli "整理以下材料，写一份带引用的报告" --flow research `
  --import-file D:\资料\笔记.md --import-text "补充粘贴。"

# 产物位置（workspaces\jobs\job_<id>\）
#   sources.json / sources\*           导入的资料（B3/B4）
#   evidence.json                      证据库（定位/摘录/标注）
#   artifacts\material_pack.v1.md      素材包（主题/冲突/缺口/重复）
#   artifacts\outline.v1.md            提纲（章节+必需证据）
#   artifacts\report.v1.md             初稿（引用 [E-编号]；修订后 v2/v3…）
#   artifacts\review.v1.json           审校问题与判定
#   pipeline.json                      阶段快照与结果分级
# job.json 含 pipeline 摘要与 import 摘要

# 退出码：accepted=0；draft/failed=1（待完善草稿也是 1，不冒充成功）
```

## 验证证据

- 全量：`.venv\Scripts\python -m pytest`，347 passed，34.94秒（B4基线330）。
- 定向：链上阶段12（quote定位/去重守卫/引用章节标注/修订一次转干净/顽固错误转draft/
  预算停止保留partial/无来源与无证据提示）+ 应用flow 5（端到端accepted并核对账本用途、
  无资料不发起模型调用、CLI退出码、Web守卫、flow校验）。
- 修订不覆盖：report.v1 与 report.v2 并存且内容不同，均可用 artifact_id 读取。
- 20个业务案例和10个故障案例定义仍有效，业务执行0次；无付费模型请求、无新增第三方依赖、未修改DSH。

## 兼容性、限制与回退

- 新增 `src/application/pipeline/` 与 TaskRequest.flow（默认 agent，行为不变）；research 分支不创建
  run 目录（run_ids 为空），Web Dashboard 暂不展示链任务（S5 接链入口与进度时处理）。
- 链在 Mock/桩 LLM 下离线验收；真实模型的结构化输出质量、长资料窗口自适应、20业务案例
  执行与人工评分待 S6。整理与写作共用同一固定链（输出形态由任务目标引导）；改稿/追问
  需要 S4 会话；"按需读取完整材料"的模型读取工具未开放（当前按预算给引文+摘录）。
- evidence.json/artifacts.json/pipeline.json 是 JSON 原子写，非多文件事务（S4 改进）。
- 回退：删除 pipeline 包与 request/research/cli/workbench 的 flow 改动即可；不要删除用户运行产物。

下一步 S4：SQLite 任务/会话/审批/操作账本、队列取消与恢复边界；随后 S5 工作台接入链入口。
