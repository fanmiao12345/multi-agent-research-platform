# 项目工作约定（给所有 AI / 协作者）

## 实用化开发记录（用户要求，2026-09-09）

- 按 `docs/PROJECT_MASTER_PLAN.md` 推进完整个人 AI 智能体系统；`docs/PRACTICAL_RESEARCH_WRITING_PLAN.md` 是首个研究写作场景说明，逐项状态见 `docs/IMPLEMENTATION_TRACKER.md`。
- 用户要求（2026-09-11）：先完成 D0～D10 全部功能并冻结，再执行 Q1～Q4 整体测试与优化。开发中只做必要的最小功能/关键边界验证和阻塞性修复；不反复跑全量、真实批次或局部调参。非阻塞优化登记 `docs/OPTIMIZATION_BACKLOG.md`。
- 每完成一步，立即在 `docs/IMPLEMENTATION_LOG.md` 追加日期、步骤编号、修改、验证结果、限制与下一步。
- 每批完成同步 README 和 EXECUTION_STATUS；未验证不能标已验收，离线测试不能替代真实业务验收。
- 历史 S/B/01～130 编号与排期只用于追溯；按总计划映射新步骤。不能把模块存在、Mock 通过或固定链完成写成整个项目完成。

## 第一条：一切改动都属于「这个项目」

- 用户说「给这个项目加 / 改 / 配 / 写一个…」时，一律指 **本目录（agent-mvp）** 内的内容：
  `agent.py`、`llm.py`、`tools.py`、`config.py`、`config.ini`、`skills/`、`questions.txt`、
  `README.md` 以及后续新增的任何文件。
- 用户后续要求添加的功能、技能、配置、文档，默认都保存到 `agent-mvp/` 下面，
  并尽量保持「零第三方依赖、结构简单、注释教学化」的现有风格。

## 第二条：不要动「本体」

- 不得为了本项目需求去修改或新增：
  - DeepSeek Harness（DSH）本体及其实时运行环境（如 @deepseek-ai/dsh、apps/web 等）
  - 任何插件 / 技能包仓库（如工作区里的 awesome-dsh-plugin-main）
  - 用户全局配置与状态（如 ~/.dsh、环境变量、DSH 网关设置）——只允许只读排查
- 判断标准：如果某样东西不是我们亲手创建在 agent-mvp 里的，默认不属于改造范围。

## 第三条：不确定就先问

- 如果一条需求既能落在 agent-mvp 里、又可能涉及「本体」，先向用户确认清楚再动手，
  不要自作主张扩展范围。
