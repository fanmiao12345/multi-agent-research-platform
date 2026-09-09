# 项目接口约定

本项目（agent-mvp）所有真实模型调用走 OpenAI 兼容接口。
- 默认供应商：DeepSeek 官方，base url = https://api.deepseek.com
- 默认对话模型名：deepseek-chat（工具调用稳定）
- 配置只从 .env 读取，不写死在源码；.env.example 只放字段名。
- 测试与离线演示一律使用 MockLLM，不依赖网络与 token。
