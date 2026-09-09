# S4交付记录：持久化状态、队列租约、审批、幂等与阶段恢复

日期：2026-09-09。已验收范围：离线代码（SQLite/队列/审批/幂等/链恢复）；常驻队列执行器
与 Web 接线（提交/取消/审批/会话页面）属 S5，跨进程租约压力测试与真实模型验收属 S6。

## 交付

1. **SQLite 状态库（S4-01/13）**：`src/harness/state/db.py`。
   workspaces/state.sqlite：jobs / sessions / session_jobs / approvals / operations + meta.schema_version。
   WAL、check_same_thread=False + 全局写锁（write_tx）；损坏文件与高于支持版本的库抛显式
   StateDbError（不重建掩盖历史）；在线备份（SQLite backup API）+ verify_backup。
   旧 run/plan JSON 布局不变可读（B2/B3 回归证明），不搞两套可写状态。
2. **状态词与映射（S4-02）**：states.py 九种业务状态 + 合法迁移表 + 与底层 Run 状态显式映射；
   终态不可回写，未知状态显式报错。
3. **有界队列与租约（S4-03 组件）**：queue.py 提交即落库；领取=单条原子 UPDATE
   （queued/interrupted 或租约过期者），并发/重启下同一任务只有一个执行者；心跳续期；
   租约过期由 startup_scan 标 interrupted 并清租约。跨进程靠 UPDATE 原子性兜底。
4. **两段取消（S4-06 队列+research链）**：queued→直接 cancelled（stopped）；
   运行中→cancel_requested（requested），链在阶段边界查询 should_stop 后收敛为
   cancelled/partial（已停止），产物保留不假装完成。
5. **操作账本（S4-08）**：ops.py pending/running/succeeded/failed/unknown；
   幂等键 op_key=job:action:version:params_hash；只有 succeeded 已知成功才回放，
   unknown（崩溃遗留）拒绝自动重放并要求核实；失败只记异常类型。
6. **持久化审批（S4-09 库侧）**：approvals.py 绑定 动作+参数哈希+scope+版本+到期；
   同动作新审批自动失效旧 pending；invalidate_for 支持重规划/取消失效；
   有效授权判定=未过期+granted+哈希/范围/版本全匹配（不凭审批者字符串放行）。
7. **会话隔离（S4-05 库侧）**：sessions.py 目标+任务归属；材料快照只登记引用，
   默认不跨会话共享资料；快照损坏显式报错。
8. **阶段检查点与恢复（S4-04/12）**：runner 顺序=产物落盘→检查点 stage_*.json→
   stage_hook 外部状态提交（先文件后状态）；已有检查点的目录拒绝无意识重跑；
   resume=True 跳过已完成阶段（evidence.json 为证据恢复源，不重复抽取）。
   resume_research_job + CLI `--resume-job`：同一 job 目录续跑，续接账本载入原调用
   （次数/输出Token/费用延续原上限），未知用量如实阻止续跑（真实模式）。
9. **能力组件（S4-07/11）**：control/subprocess_guard.py 有界子进程（超时真实 kill、
   输出截断）；control/progress.py 按 (llm调用数, 产物哈希) 判定无进展。
10. **修复**：Mock 适配器失败=已知零成本（不污染 unknown 用量账）；真实模式失败
    仍阻止后续调用 —— 崩溃续跑在 Mock/桩下可离线验收。

## 使用

```powershell
# 队列与租约（供 S5 执行器/Web 使用；现在可直接在代码中调用）
#   JobQueue(db).submit(request=...) / claim(owner, lease_seconds=...) /
#   request_cancel(job_id) / resume_after_stop(...) / startup_scan(...)

# 研究写作链：真实进程崩溃后续跑（同任务目录，跳过已完成阶段）
.venv\Scripts\python -m src.interfaces.cli --workspace workspaces --resume-job job_<id>
# 注意：默认 Mock 不能产出链式结构化 JSON → 明确 failed（不假成功）；
# 真实验收需 .env 配置真实模型。函数级入口 resume_research_job 供桩/真实大脑注入。
```

产物：`workspaces/state.sqlite`（+ -wal/-shm）；`workspaces/jobs/<job_id>/stage_*.json`
阶段检查点；`pipeline.json` 结果快照。

## 验证证据

- 全量：`.venv\Scripts\python -m pytest`，372 passed，39.42秒（B5基线347）。
- 定向：状态核心14（schema/损坏/高版本/备份、迁移表、并发唯一赢家、租约过期回收、
  取消两段、幂等回放与 unknown、审批绑定/过期/scope、会话隔离）；
  S4链层/能力8（真实子进程 os._exit 崩溃→resume accepted 且证据只抽取一次、
  检查点重跑拒绝、等价崩溃窗口、取消收敛、钩子先文件后状态、子进程超时/kill/截断、
  无进展检测）；续跑入口3（函数级 accepted+账本续接、损坏显式错误、CLI 语义）。
- 20个业务案例和10个故障案例定义仍有效，业务执行0次；无付费模型请求、无新增第三方依赖、未修改DSH。

## 兼容性、限制与回退

- 新增目录 `src/harness/state/`、`control/{subprocess_guard,progress}.py`、`tests/_s4_pipeline_brain.py`；
  runner 参数全带默认值（resume=False/should_stop=None/stage_hook=None），原调用不变；
  Mock 失败记账语义变化只影响失败路径且零成本记账更诚实。
- 未做：常驻队列执行器与 Web 提交/取消/审批/会话接线（S5）；Agent 通用循环的取消门与
  无进展看门狗接线（S5 执行器内）；跨进程租约并发压力与真实模型验收（S6）；
  节点级官方持久化 Checkpointer（决策：业务阶段恢复边界足够，避免两套调度）。
- 续接账本的 elapsed_seconds 从续跑时点起算（历史耗时不并入展示）；真实模式崩溃遗留的
  unknown 调用必须人工核实后才可续跑（设计使然，不自动重放）。
- 回退：删除 state/ 与新增 control 文件、还原 runner/CLI/research/model_gateway 改动即可；
  state.sqlite 与 jobs 产物不要删除。

下一步 S5：完整工作台（含 S4 队列/取消/审批/会话的页面接线）。
