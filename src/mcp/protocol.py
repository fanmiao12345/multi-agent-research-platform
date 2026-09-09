# -*- coding: utf-8 -*-
"""
mcp/protocol.py —— MCP 消息协议（JSON-RPC 2.0，行分隔 JSON）

学习用零依赖实现：请求/响应/错误信封 + 工具方法名常量。
真实部署可换官方 MCP SDK；本实现保证同一套语义。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

JSONRPC = "2.0"

INITIALIZE = "initialize"
TOOLS_LIST = "tools/list"
TOOLS_CALL = "tools/call"
NOTIFICATIONS_INITIALIZED = "notifications/initialized"


@dataclass
class RpcError(Exception):
    code: int
    message: str
    data: dict | None = None

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                **({"data": self.data} if self.data else {})}


def make_request(msg_id, method: str, params: dict | None = None) -> str:
    payload = {"jsonrpc": JSONRPC, "id": msg_id, "method": method}
    if params is not None:
        payload["params"] = params
    return json.dumps(payload, ensure_ascii=False)


def make_response(msg_id, result) -> str:
    return json.dumps({"jsonrpc": JSONRPC, "id": msg_id, "result": result},
                      ensure_ascii=False)


def make_error(msg_id, error: RpcError) -> str:
    return json.dumps({"jsonrpc": JSONRPC, "id": msg_id, "error": error.to_dict()},
                      ensure_ascii=False)


def parse(line: str) -> dict:
    """解析一行 JSON-RPC；非法 JSON 抛 RpcError(-32700)。"""
    try:
        return json.loads(line)
    except json.JSONDecodeError as e:
        raise RpcError(-32700, f"Parse error: {e}") from e


def error_from(msg: dict) -> RpcError | None:
    if "error" in msg:
        e = msg["error"]
        return RpcError(e.get("code", 0), e.get("message", ""), e.get("data"))
    return None
