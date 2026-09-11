# 本地 MCP 使用说明

## Server 能力

本地 Server 暴露两个受控只读工具：

- `workspace_search(query)`：检索工作区历史产物文本。
- `memory_search(query)`：检索显式保存的长期记忆。

启动：

```powershell
.venv\Scripts\python -m src.mcp.local_server
```

stdio JSON-RPC 协议仅用于本地子进程接入，不监听公网端口。

## Client 接入

在 `.env` 中配置 `MCP_SERVERS` JSON 数组，例如：

```json
[{"name":"local","command":".venv/Scripts/python.exe","args":["-m","src.mcp.local_server"],"allow_tools":["workspace_search","memory_search"],"default_side_effect":false}]
```

应用启动时发现并注册工具，名称为 `mcp:<server>:<tool>`。工具执行继续经过统一权限、风险、审批、超时和日志链。

- allowlist 未授权工具不会注册。
- 未知外部操作默认 `side_effect=true`，不盲目自动重试。
- Server 生命周期随应用运行关闭；异常启动不静默降级。
- 不把模型生成的报告当原始事实，工具返回内容仍按来源/证据规则处理。