# S5交付记录：研究任务 Web 工作台

日期：2026-09-09。已验收范围：离线 HTTP/页面代码（含队列生命周期、取消、恢复、证据引用、
版本与导出、写接口安全）；真实浏览器人工验收与真实模型长任务未执行（S6）。

## 交付

1. **入口注入（S5-01）**：`ResearchApplication.run` 支持 `job_id`（白名单校验、目录已存在拒绝）、
   `on_progress`/`stage_hook`/`should_stop` 注入——研究链的阶段进度、边界取消与"先文件后状态"
   钩子可被 Web 队列执行器使用。
2. **Web×S4 队列接线（S5-02）**：WorkbenchState 挂 `state.sqlite` + JobQueue + 常驻 worker：
   研究任务 `POST /api/runs (flow=research)` → queued → 领取 → `ResearchApplication.run(job_id=…)`
   → 按结果分级 release（completed/partial/cancelled/failed）；领取前已取消直接收敛；
   运行中取消在链阶段边界生效并保留已落产物。
3. **任务视图（S5-02/03）**：`/api/jobs` 列表、`<id>/progress`（队列行+job.json+pipeline.json）、
   `<id>/evidence`（证据+摘录+字符/段落定位）、`<id>/artifacts/<aid>/download`（附件下载）。
   页面㉑卡片：任务列表/阶段与分级日志/停止/恢复/导出；报告按 [E-编号] 分词渲染成按钮，
   点击显示该证据的原文摘录与定位；报告版本列表（report.v1/v2…旧稿不覆盖）可分别查看。
4. **写接口安全（S5-09）**：Host 必须为 127.0.0.1/localhost/::1（否则403）；Content-Length
   ≤1MB（413）；Content-Type 仅 application/json（415）；服务默认只绑定 127.0.0.1。
   渲染全走 textContent/DOM（脚本不执行）；下载仅限任务内登记产物并带
   `Content-Disposition: attachment` + `X-Content-Type-Options: nosniff`。
5. **可操作错误（S5-07）**：提交/导入/链分级消息带可读文案贯穿 HTTP 响应与页面（保留类型+message）。

## 使用

```powershell
# 启动页面（默认回环绑定）
.venv\Scripts\python -m src.interfaces.web.workbench --port 8765
# 打开 http://127.0.0.1:8765/ → 表单选择“研究写作链”→ 填任务/资料 → 运行
# ㉑研究任务卡跟踪：排队→阶段进度→分级；可停止/恢复；报告正文引用可点开证据原文定位；
# 版本列表查看 + “下载最新 Markdown”；重启服务后任务仍可查看（state.sqlite + job 目录）。
# 注：默认 Mock 不能产出链式结构化内容 → 任务会以明确错误/失败呈现；真实模式需配置 .env。
```

## 验证证据

- 全量：`.venv\Scripts\python -m pytest`，378 passed，46.61秒（S4基线372）。
- 定向新增：tests/test_workbench_s5.py 6 passed——研究任务完整生命周期（queued→completed，
  报告/证据/下载头）、运行中取消收敛（stage=evidence:completed 后取消→cancelled 且证据保留）、
  失败后恢复（202→accepted）、写接口安全三连（Host/大小/类型）且读接口不受限、
  重启后同一状态库任务可查、页面 S5 标记存在。
- 既有 workbench/research/imports/pipeline suites 无回归；20业务/10故障案例定义仍有效（执行0次）。

## 兼容性、限制与回退

- 新增依赖：无（标准库 sqlite3）。行为变化：`flow=research` 的 /api/runs 由"400 暂未接入"变为
  队列提交（返回 job_id）；旧 agent 流程与端点不变。
- 限制：agent 流程仍直跑不进队列；队列 worker 单实例（无多实例心跳续期，S6 压力复验）；
  追问改稿（会话式继续）、审批"失效原因"展示页与常规读取授权记忆、读者/长度/高级参数折叠、
  大日志分页、HTML 富文本预览、搜索配置状态收口未做；真实浏览器人工验收未执行。
- 回退：还原 workbench/research/cli 改动并删除 state.sqlite（用户任务产物 jobs/ 不要删）。

下一步 S6：真实评测/安装/个人试用（含浏览器人工验收）。
