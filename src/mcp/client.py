# -*- coding: utf-8 -*-
"""
mcp/client.py —— MCP Client（步骤 104/106-108）

- StreamRPC：基于 send/recv 两个可读/可写流的 JSON-RPC 客户端（stdio 场景）
- MCPClient：initialize / tools/list / tools/call + 结果归一化（content 文本拼接）
- discover_to_registry：把远端工具注册进 Harness ToolRegistry（带安全映射），
  工具函数在 Executor 内执行 → MCP 工具不绕过权限/风险检查（步骤 109）。
"""

from __future__ import annotations

import json
import uuid

from src.harness.tools.registry import RISK_MEDIUM, ToolRegistry, ToolSpec
from src.mcp import protocol as p
from src.mcp.security import McpSecurity


class StreamRPC:
    """在 (send(text), recv()->text|None) 之上做同步 JSON-RPC 调用。"""

    def __init__(self, send, recv, timeout_lines: int = 200):
        self._send, self._recv = send, recv
        self._seq = 0
        self._timeout = timeout_lines

    def call(self, method: str, params: dict | None = None) -> dict:
        self._seq += 1
        msg_id = self._seq
        self._send(p.make_request(msg_id, method, params))
        for _ in range(self._timeout):
            line = self._recv()
            if line is None:
                raise p.RpcError(-32000, "连接关闭：等待响应时对端断开")
            line = line.strip()
            if not line:
                continue
            msg = p.parse(line)
            if msg.get("id") != msg_id:
                continue          # 跳过非本请求的响应
            err = p.error_from(msg)
            if err:
                raise err
            return msg.get("result") or {}
        raise p.RpcError(-32000, f"等待 {method} 响应超时")


def text_of(result: dict) -> str:
    """把 tools/call 的 content 数组归一化为纯文本。"""
    content = result.get("content") or []
    parts = []
    for item in content:
        if isinstance(item, dict):
            parts.append(item.get("text", json.dumps(item, ensure_ascii=False)))
        else:
            parts.append(str(item))
    return "\n".join(parts) or "（MCP 工具返回空）"


class MCPClient:
    def __init__(self, rpc: StreamRPC, name: str = "mcp-client"):
        self.rpc = rpc
        self.info = self.rpc.call(p.INITIALIZE,
                                  {"protocolVersion": "2024-11-05",
                                   "clientInfo": {"name": name, "version": "0.1.0"}})

    def list_tools(self) -> list[dict]:
        return (self.rpc.call(p.TOOLS_LIST) or {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        result = self.rpc.call(p.TOOLS_CALL,
                               {"name": name, "arguments": arguments or {}})
        return text_of(result)


def discover_to_registry(client: MCPClient, registry: ToolRegistry,
                         security: McpSecurity | None = None) -> list[str]:
    """发现远端工具并注册进 Harness（名字加前缀 mcp_<server>:<tool> 防冲突）。"""
    security = security or McpSecurity()
    names = []
    for tool in client.list_tools():
        name = tool.get("name", "")
        if security.deny_tools and name in security.deny_tools:
            continue
        if security.allow_tools and name not in security.allow_tools:
            continue
        risk = (security.tool_risk or {}).get(name, RISK_MEDIUM)
        registered = f"mcp:{name}"

        def fn(_name=name, **kwargs):   # Executor 用 spec.func(**arguments) 展开调用
            return client.call_tool(_name, kwargs)

        input_schema = (tool.get("inputSchema") or {})
        registry.register(ToolSpec(
            name=registered,
            description=f"[MCP:{name}] {tool.get('description', '')}",
            func=fn,
            parameters=input_schema.get("parameters") or input_schema,
            risk_level=risk,
            side_effect=False,
            timeout=30.0))
        names.append(registered)
    return names
