# multi-agent-research-console-v3

Research Console V3 的设计源目录（2026-09-22 由用户提供并合入主项目）。

| 文件 | 作用 |
|---|---|
| `UI_PRODUCT_SPEC_V3.md` | V3 界面的产品说明（信息架构：首页 / 任务中心 / 任务详情 / 运行观测 / 配置与评测） |
| `research_console_v3.html` | 界面设计源（单文件 HTML） |
| `apply_research_console_v3.py` | 一次性幂等回写脚本：把设计源转换后写入 `src/interfaces/web/workbench.py` 的 `INDEX_HTML` |

## 现状

- V3 界面**已合入** `workbench.py`（hash 路由五视图），合并时修复的 8 处差异见 `docs/EXECUTION_STATUS.md` 2026-09-22 条目。
- 本目录仅保留设计源与回写脚本，**运行时不需要它**；日常运行入口是：
  `python -m src.interfaces.web.workbench --port 8765`
- 如需从设计源重新回写界面，先读 `apply_research_console_v3.py` 确认幂等逻辑，再手动执行（不在测试与启动路径中）。
