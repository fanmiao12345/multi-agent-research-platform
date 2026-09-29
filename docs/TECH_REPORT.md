# 技术报告（TECH REPORT）
- 生成：2026-09-29T09:58:18

## 里程碑验收映射
| 里程碑 | 交付 | 落点 |
|---|---|---|
| A0 | 从零 Graph + Tool Loop + Mock/真实 Adapter | hello_graph/agent_loop/MockLLM/OpenAI 适配 |
| A1 | Single-Agent Harness（Lifecycle/State/Reducers） | runtime/lifecycle/run_context/state |
| A2 | Eval+Trace 先行 | benchmark/trace/usage 报告 |
| M3 | Planning（Planner/TaskGraph/Scheduler/Replanner） | planning/* + metrics |
| M4 | Tool/Skill Harness + Subagent | control/policy、skills/、subagent 工具 |
| M5 | Context Engineering | builder/policy/budget/compressors/handoff + eval |
| M6 | Memory & Knowledge | checkpointer/long_term/policy/knowledge + eval |
| M7 | Multi-Agent Orchestration | orchestration/* 六策略 + benchmark |
| M8 | Reliability + HITL | durable/loop_guard/guardrails/hitl/state_review 实验 |
| M9 | Model Routing + Budget | models/* + budget_control + 三档验收 |
| M10 | MCP | 零依赖 JSON-RPC Client/Server + 安全映射 + 子进程集成 |
| M11 | Web Workbench | workbench REST API + Research Console V3 单页前端（2026-09-22 起，历史为 9 面板版） |
| M12 | 最终实验与文档 | 本报告 + README/architecture |

## 质量证据
- 自动化测试：pytest 637 passed（组件/轨迹/端到端/HTTP/子进程/故障实验）
- 运行留痕：每次 run 有 run.json/trace.jsonl/usage.json；benchmark 有 json+md 报告
- 设计原则遵守：D-003 Mock First（全部评测离线可复现）；D-004 Runtime 与 Policy 分离；D-006 本地 Trace 优先

> 注：本报告汇总离线 Mock 评测基线；真实模型端的质量/成本读数见 eval/reports/ 各真实批次报告（q2_real_batch、q2_web_baseline、q3_*、q4_trial）与 docs/EXECUTION_STATUS.md。
