# -*- coding: utf-8 -*-
"""
mcp/server.py —— MCP Server 框架（步骤 105 / J3 配套）

一个 Server = 一组 Tool（name/description/inputSchema/fn）。
stdio 循环：逐行读 → 处理 → 回写（Initialize 握手 + tools/list + tools/call）。
"""

from __future__ import annotations

import sys

from src.mcp import protocol as p


class MCPServer:
    """本地 MCP Server：注册工具 + 处理 JSON-RPC 消息。"""

    def __init__(self, server_name: str = "local", server_version: str = "0.1.0"):
        self.name = server_name
        self.version = server_version
        self._tools: dict[str, dict] = {}

    def register(self, name: str, description: str, fn, input_schema: dict | None = None):
        self._tools[name] = {"name": name, "description": description,
                             "inputSchema": input_schema or {"type": "object",
                                                             "properties": {}},
                             "fn": fn}

    def list_tools(self) -> list[dict]:
        return [{k: v for k, v in t.items() if k != "fn"} for t in self._tools.values()]

    # ---- 消息处理 ----
    def handle_line(self, line: str) -> str | None:
        """处理一行请求，返回要写回的一行（通知类返回 None）。"""
        try:
            msg = p.parse(line)
        except p.RpcError as e:
            return p.make_error(None, e)
        if "method" not in msg:          # 响应（本服务端作为 server 不会收到）
            return None
        msg_id = msg.get("id")
        method = msg["method"]
        params = msg.get("params") or {}
        try:
            if method == p.INITIALIZE:
                result = {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": self.name, "version": self.version}}
            elif method == p.TOOLS_LIST:
                result = {"tools": self.list_tools()}
            elif method == p.TOOLS_CALL:
                name = (params.get("params") or {}).get("name") or params.get("name")
                arguments = (params.get("params") or {}).get("arguments") \
                    or params.get("arguments") or {}
                tool = self._tools.get(name)
                if tool is None:
                    raise p.RpcError(-32602, f"未知工具 {name}",
                                     {"available": list(self._tools)})
                text = str(tool["fn"](**arguments))
                result = {"content": [{"type": "text", "text": text}],
                          "isError": False}
            elif method == p.NOTIFICATIONS_INITIALIZED:
                return None
            else:
                raise p.RpcError(-32601, f"未知方法 {method}")
            return p.make_response(msg_id, result)
        except p.RpcError as e:
            return p.make_error(msg_id, e)
        except Exception as e:           # noqa: BLE001 —— 工具错误要进 result 而非 error?
            return p.make_error(msg_id, p.RpcError(-32603, f"内部错误：{e}"))

    # ---- stdio 主循环 ----
    def serve_stdio(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            out = self.handle_line(line)
            if out is not None:
                stdout.write(out + "\n")
                stdout.flush()
