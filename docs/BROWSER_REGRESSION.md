# 浏览器回归清单（S6-09，说明级）

> 浏览器回归工具（如 Playwright）作为**开发依赖**候选引入，不混入生产依赖。
> 引入前核对环境与版本并按官方文档锁定；引入后在 CI/试用期执行本清单。
> 现阶段（S6 离线批次）由 HTTP 级测试覆盖等价行为，人工浏览器验收在 7 天试用期执行。

## 已引入：Playwright（2026-09-18，React 页先行）

- 版本：playwright 1.63.0 + Chromium 153.0.8010.12（`pip install playwright && python -m playwright install chromium`）；
  已加入 `pyproject.toml` 的 `dev` 依赖组，不进生产依赖。
- 测试：`tests/browser/test_react_page.py`（playwright 未装时整文件 skip，不影响离线回归）。
- 覆盖：React 页正常旅程（渲染→逐键输入→提交→轮询到终态→截图
  `eval/reports/react_browser/react_page_final.png`）+ 回退旅程（vendor 与 CDN 全部
  不可达时显示明确回退提示，不白屏）。验收过程中发现并修复两个真实缺陷：
  ① FastAPI 适配层未挂 `/vendor` 静态路由（本地运行时全 404 回落 CDN）；
  ② React 页 `useState` 解构错误导致输入/提交完全失效——浏览器级验收的价值实证。
- 边界：React 页是 FastAPI 适配层的演示前端；标准库工作台的浏览器清单仍按原计划
  在 7 天试用期人工执行。

## 覆盖矩阵（正文/引用/审批/取消/改稿/导出）

| 场景 | 页面/入口 | 自动断言要点 | HTTP级覆盖(现有) | 人工/浏览器 |
|---|---|---|---|---|
| 提交→进度→报告 | 表单(flow=research) + ㉑任务卡 | queued→阶段→分级；刷新/重启可查看 | test_workbench_s5 | 待执行 |
| 来源与引用对照 | reportview/rtok/evidenceview | 点 [E-编号] 显示摘录/定位；无脚本执行 | test_workbench_s5(数据) | 待执行 |
| 停止任务 | ㉑停止按钮 | 阶段边界收敛，产物保留 | test_workbench_s5 | 待执行 |
| 恢复任务 | ㉑恢复按钮 | 中断任务续跑 accepted | test_workbench_s5 | 待执行 |
| 审批 | HITL 面板 | 展示动作/参数；批准/拒绝生效 | test_workbench | 待执行 |
| 改稿 | 单次改稿与追问改稿已接通（CLI --revise-job / Web revise，revises_job 谱系；HTTP 级有等价覆盖） | — | — | 浏览器人工验收随试用补 |
| 导出 | 下载 Markdown | 附件头；内容仅任务内产物 | test_workbench_s5 | 待执行 |
| 安全字符串 | 报告/来源渲染 | 文本不当作 HTML 执行 | test_workbench(HTML) | 待执行 |
| **React 页正常旅程** | FastAPI 适配层 `/`（React 18 vendor 本地） | 渲染/提交/轮询到终态/截图；无控制台错误 | test_fastapi_app | **已执行（2026-09-18，tests/browser/test_react_page.py）** |
| **React 页回退提示** | 同上（vendor+CDN 全部不可达） | 明确回退文案指向零依赖工作台，不白屏 | — | **已执行（同上）** |

## 引入步骤（当决定需要时）

1. 把浏览器测试工具加进 `dev` 依赖组并锁定版本（新环境安装后跑 `python -m src.ops.verify`）。
2. 新增 `tests/browser/`，用同一套行为断言驱动页面（不要另起更简单的页面）。
3. 在 7 天试用期把本清单逐行打勾；发现的问题转可复现用例后归入 `eval/datasets/`。
