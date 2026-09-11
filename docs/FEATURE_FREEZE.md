# 候选版与功能冻结清单

更新：2026-09-11。冻结对象为当前工作树；精确文件哈希、依赖哈希和 Git 版本由：

```powershell
.venv\Scripts\python -m eval.freeze_manifest
```

生成到 `eval/reports/freeze_manifest.json`。

## G01～G15 对照

| 能力 | 主要实现 | 状态 | 最小证据 |
|---|---|---|---|
| G01 统一任务与会话入口 | TaskRequest、CLI、Web、SessionStore | 完成 | `test_application.py`、`test_workbench_s5.py` |
| G02 单智能体执行 | AgentRuntime、single 模式 | 完成 | `test_harness_runtime.py`、`test_d6_integration.py` |
| G03 工具与技能生效 | ToolExecutor、SkillRegistry、角色权限交集 | 完成 | `test_milestone4.py`、`test_d4_integration.py` |
| G04 主动获取资料 | bing_scrape、正文抓取 | 完成 | `test_search_bing.py`、`test_web_research.py` |
| G05 来源、证据与产物 | SourceStore、EvidenceStore、ArtifactStore、引用谱系 | 完成 | `test_source_library.py`、`test_d7_integration.py` |
| G06 任务理解与规划 | understanding、planning、scheduler | 完成 | `test_d5_integration.py`、`test_planning.py` |
| G07 六种协作方式 | application/orchestration/registry.py | 完成 | `test_d6_integration.py` |
| G08 子智能体与嵌套 | delegate_subagent、深度/总量/去重/权限护栏 | 完成 | `test_d6_integration.py`、`test_milestone4.py` |
| G09 上下文、记忆与知识 | Context Builder、LongTermStore、knowledge retrieve | 完成 | `test_context.py`、`test_memory.py`、`test_d4_integration.py` |
| G10 全任务预算、模型与权限 | JobLedger reserve/settle、根账本记录 | 完成 | `test_application.py`、`test_orchestration_s8.py` |
| G11 自动检查与有限补做 | 程序/模型审校、repair_callback | 完成 | `test_pipeline_stages.py`、`test_d8_integration.py` |
| G12 持久化、取消、恢复与审批 | StateDb、JobQueue、pending_inputs、ApprovalStore | 完成 | `test_state_core.py`、`test_d8_integration.py`、`test_d9_integration.py` |
| G13 工作台与交付 | Web 工作台、版本、HTML/Markdown、过程记录 | 完成 | `test_workbench_s5.py`、`test_d9_integration.py` |
| G14 MCP 与扩展接口 | MCP bootstrap/security/local server | 完成 | `test_mcp_bootstrap.py`、`docs/MCP_USAGE.md` |
| G15 安装运维与可信评估 | ops health/verify/backup/restore、业务评测 | 完成 | `test_ops_s6.py`、`docs/INSTALL_AND_RECOVERY.md` |

## 冻结边界

- 已实现：D0～D10 的开发功能最小接入与离线验证。
- 未实现且不属于本轮必做：OCR、DOCX 输入、向量数据库、生产级多用户部署、任意外部副作用 exactly-once。
- 质量/效率/体验参数优化统一进入 Q3，不在冻结前继续改功能。
- 真实模型、真实联网质量、真实浏览器全旅程和个人试用分别由 Q1-03、Q2、Q4 验证。
- 候选版不得用 Mock、组件存在或历史批次成绩代替真实业务验收。