# S6交付记录：真实评测运行器、人工评分与运维（离线可验收部分）

日期：2026-09-09。已验收范围：评测/评分/运维/模板的离线代码与行为；**真实模型 60 次业务运行、
联网冒烟、故障两轮全量执行、人工评分与 7 天试用需真实 Key/预算与人力，本批未执行**（已全部标注）。

## 交付

1. **业务评测运行器（S6-01/03/04/06）**：`eval/business_eval.py`
   - 每个案例都走 `ResearchApplication(flow=research)`（不另写更简单的评测路径）；
   - `--mode real` 必须给 `--max-cost`；配置不可用（缺 Key 等）时**整批 not_executed 并记录原因，
     零模型请求，绝不用 Mock 成绩顶替**；探针不发请求；
   - `--repeats 3`（业务）与 `--fault-rounds 2`（故障）可配置；失败样本整目录保存
     （job.json/pipeline.json/evidence.json/ledger.json/sources.json + report 产物）；
   - 逐次记录：耗时/已知或未知费用/失败原因/终止原因/分级/机器检查（引用未解析、必需章节覆盖、
     事实命中、禁语命中——标注"仅供参考，需人工评分"）；
   - 报告快照：数据集名与版本、代码版本（当前 no_git_commit_yet）、Python 与依赖包版本、
     实际模型与工具清单、脱敏配置（不含 Key/接口地址）。
2. **人工评分表（S6-05）**：`eval/human_scores.py` 按每次尝试生成 CSV——正确性/结构/引用/完整性
   1~5、人工改稿分钟、机器检查列明示；评测器不自评分盖章。
3. **运维（S6-07/08）**：`src/ops/health.py`（只读健康检查）、`src/ops/backup.py`
   （SQLite 在线备份 + jobs/sessions 复制 + manifest；恢复前须停服务）、`src/ops/verify.py`
   （导入冒烟/离线 CLI 样例/配置诊断/健康检查的新环境验证清单）；
   `requirements.lock.txt` 锁定 Python 3.14.7 与 langgraph/langchain-core/openai/pytest/
   python-dotenv 的已验证版本。日志位置、启动/诊断命令、备份恢复步骤见本文档。
4. **试用与浏览器模板（S6-09/10）**：`docs/TRIAL_LOG_TEMPLATE.md`（20+ 任务、每日自查、
   问题转可复现用例、1.0 判定步骤）、`docs/BROWSER_REGRESSION.md`（覆盖矩阵+开发依赖引入步骤）。

## 使用

```powershell
# 业务评测（默认 mock：Mock 不能产出链式 JSON → 会诚实失败并保存样本；真实评测需 .env）
python -m eval.business_eval --mode real --max-cost 0.1 --repeats 3 --fault-rounds 2
# 输出：eval/reports/business*/business_report.json|.md（真实无 Key 时整批 not_executed）

# 人工评分表
python -m eval.human_scores --report eval/reports/business_*/business_report.json --out eval/reports/business_*/sheets

# 运维
python -m src.ops.health                     # 健康检查（只读）
python -m src.ops.backup --workspace workspaces --out .tmp\backup   # 备份
python -m src.ops.verify                     # 全新环境验证清单
pip install -r requirements.lock.txt         # 锁定依赖安装（可选）
python -m src.harness.models.factory --mode real   # 配置诊断（不发请求）

# 日志位置：workspaces/jobs/<job_id>/{job.json,ledger.json,pipeline.json,evidence.json,trace}
#          workspaces/runs/<run_id>/{run.json,trace.jsonl,usage.json}
# 恢复：先停止服务，再把备份目录内容复制回工作区；不要覆盖正在运行的工作区。
```

## 验证证据

- 全量：`.venv\Scripts\python -m pytest`（随批末回归，378+9=387 预期）。
- 定向新增 tests/test_ops_s6.py 9 passed：real 缺 Key → 整批 not_executed 且零账本、
  stub 大脑 2/2 accepted 并带机器检查与快照（不含密钥）、Mock 诚实失败且失败样本落盘、
  改稿案例明确跳过、故障配置探针、评分表生成、健康检查、备份 roundtrip（sqlite 校验可打开）、
  新环境验证清单可运行。

## 限制与待执行（不因有工具就打勾）

- 真实模型 60 次业务运行、联网冒烟、故障案例全量两轮、人工评分与导入、
  单 Agent/手工基线对比、7 天试用（20+ 真实任务）与 1.0 判定——需要你配置 `.env`、
  设批次限额并投入时间执行；模板与运行器已就绪。
- 改稿案例在"会话式改稿链"接通前一律明确跳过（不冒充通过）。
- 浏览器回归工具未引入（说明级清单）；全新虚拟环境安装验证需在干净机器执行 verify。
- Git 基线仍为 no_git_commit_yet：真实评测前建议先做 S0-06 首次提交，保证报告可回退对照。

下一步 S7：仅在证据显示瓶颈/漏召回/无收益时才做的有界并行、检索优化、模型路由等；
以及 S0-06 Git 基线、S2 搜索服务商、追问改稿与试用执行等随用户安排。
