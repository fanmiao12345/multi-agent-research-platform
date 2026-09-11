# Q1-03 真实浏览器与安装恢复记录

- 浏览器：Codex In-app Browser
- 地址：`http://127.0.0.1:8877/`
- Agent 旅程：`计算6*7`，页面显示 `6*7 = 42`。
- 研究旅程：真实模型 `deepseek-v4-flash`，合成资料，任务 `整理资料目录`；job `job_44fbdf0f17d34b0fb16c82f5686fd7d2`，`accepted`，2 条可定位引用，产物 `collection.v1`。
- 失败状态：Mock 研究链的规则大脑不能完成证据 JSON 阶段，页面明确显示失败原因与阶段。
- 刷新/重启：服务重启后 run/job 历史仍可读取。
- 导出：页面提供 Markdown、HTML、过程记录三个链接。
- 安装验证：`python -m src.ops.verify` 通过。
- 备份恢复：状态库/目录备份恢复测试通过。
- MCP：本地 server 配置、发现、调用和生命周期测试通过。
- 局限：本轮只做功能可用性，不代表报告业务质量；浏览器视觉和体验优化留 Q3/Q4。