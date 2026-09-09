# 常用术语表

- ReAct：Reason + Act -> Observe 的循环：模型思考、调用工具、观察结果、再思考。
- Skill：一段可复用的方法论手册（Markdown + Frontmatter），命中时注入上下文。
- Runtime Context：一次运行携带的静态参数（模型/权限/预算/工作区等）。
- Handoff Pack：Agent 之间交接时传递的结构化上下文包（不传整段对话）。
- Loop Guard：迭代/重试上限保护，防止 Agent 死循环与无限返工。
