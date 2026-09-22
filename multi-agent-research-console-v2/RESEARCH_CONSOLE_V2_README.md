# Research Console V2

这是针对 `fanmiao12345/multi-agent-research-platform` 当前 Workbench 做的完整前端重构版，保持后端 API 和核心 DOM ID 不变。

主要变化：

- 从“8个同权重调试卡片”重构为真正的 Research Console。
- 左侧应用导航：新建研究、研究任务、来源与产物、运行记录、Trace、Eval。
- 新建任务只展示核心输入，高级参数、来源和硬约束全部折叠。
- 研究任务增加阶段 rail + 总进度条。
- 报告区域改成阅读器，对 `# / ## / ### / -` 做安全 DOM 渲染，不执行 HTML。
- `[E-001]` 这类证据引用保留为可点击按钮。
- 来源、产物、证据集中到右侧检查面板。
- Run / Trace / Tool / HITL 合并成观测区，不再抢占研究主流程。
- 修复原前端的三个问题：
  1. `viewArtifact()` 对 `text/plain` 使用 JSON 解析。
  2. “导出 Markdown”按钮缺少 `exportReport()`。
  3. 原页面末尾重复 `<script>` 标签存在脚本解析风险。
- 改善任务轮询：切换 job 时旧的 `pollJob()` 会通过版本号自动退出，减少重复轮询。
- 保持动态内容全部使用 `textContent` / DOM API，避免把模型输出直接当 HTML 执行。

## 应用

把以下两个文件放到仓库根目录：

- `research_console_v2.html`
- `apply_research_console_v2.py`

运行：

```bash
python apply_research_console_v2.py
python -m py_compile src/interfaces/web/workbench.py
python -m pytest tests/test_workbench.py tests/test_workbench_b3.py tests/test_workbench_s5.py -q
python -m src.interfaces.web.workbench --port 8765
```

打开：

```text
http://127.0.0.1:8765/
```

## 回退

脚本会自动生成：

```text
src/interfaces/web/workbench.py.before-research-console-v2.bak
```

也可以执行：

```bash
git restore src/interfaces/web/workbench.py
```

## 说明

当前环境的 GitHub 连接可以读取仓库，但写入、新建分支都会被 GitHub App 权限返回 403，因此无法直接替你 push。这个包是按仓库当前 `master` 的 `INDEX_HTML` 结构设计的，修改范围只替换前端常量，不改后端业务逻辑。
