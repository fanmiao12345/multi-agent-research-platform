# -*- coding: utf-8 -*-
"""测试：MCP Client/Server/Security（M10 步骤 104-110）。"""
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

import pytest

from src.harness.tools.executor import (TOOL_PERMISSION_DENIED, ToolExecutor)
from src.harness.tools.registry import RISK_LOW, ToolRegistry
from src.mcp import protocol as p
from src.mcp.client import MCPClient, StreamRPC, discover_to_registry, text_of
from src.mcp.security import McpSecurity
from src.mcp.server import MCPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _loopback_server() -> tuple[MCPServer, "object"]:
    """返回 (server, rpc-like) —— 直接内联处理行，模拟 stdio 往返。"""
    server = MCPServer("test")

    class LoopbackRPC:
        def call(self, method, params=None):
            msg_id = "req-1"
            out = server.handle_line(p.make_request(msg_id, method, params))
            reply = p.parse(out)
            err = p.error_from(reply)
            if err:
                raise err
            return reply.get("result") or {}

    return server, LoopbackRPC()


# ---------- 104/105 协议与服务端 ----------
def test_server_handshake_list_and_call():
    server = MCPServer("demo")
    server.register("echo", "回显", lambda text: f"echo:{text}",
                    input_schema={"type": "object",
                                  "properties": {"text": {"type": "string"}},
                                  "required": ["text"]})
    out = server.handle_line(p.make_request("1", p.INITIALIZE,
                                            {"protocolVersion": "2024-11-05"}))
    result = p.parse(out)["result"]
    assert result["serverInfo"]["name"] == "demo"
    out = server.handle_line(p.make_request("2", p.TOOLS_LIST))
    assert p.parse(out)["result"]["tools"][0]["name"] == "echo"
    out = server.handle_line(p.make_request("3", p.TOOLS_CALL,
                                            {"name": "echo", "arguments": {"text": "hi"}}))
    assert text_of(p.parse(out)["result"]) == "echo:hi"
    out = server.handle_line(p.make_request("4", p.TOOLS_CALL, {"name": "nope"}))
    assert p.error_from(p.parse(out)) is not None
    assert p.parse(out)["error"]["code"] == -32602


def test_protocol_parse_error():
    assert p.error_from({"error": {"code": 1, "message": "x"}}).code == 1


# ---------- 106/107 Client ----------
def test_client_loopback():
    server, rpc = _loopback_server()
    server.register("echo", "回显", lambda text: f"ok:{text}")
    client = MCPClient(rpc)
    assert client.info["serverInfo"]["name"] == "test"
    names = [t["name"] for t in client.list_tools()]
    assert names == ["echo"]
    assert client.call_tool("echo", {"text": "世界"}) == "ok:世界"


# ---------- 108/109 Discovery → Registry（含 Security 映射）----------
def test_discover_to_registry_security():
    server, rpc = _loopback_server()

    def ws_fn(query):
        return f"hit:{query}"

    def danger_fn(path):
        return f"deleted:{path}"

    server.register("workspace_search", "搜工作区", ws_fn)
    server.register("danger_delete", "删除", danger_fn)
    client = MCPClient(rpc)

    security = McpSecurity(deny_tools={"danger_delete"}, tool_risk={"workspace_search": RISK_LOW})
    reg = ToolRegistry()
    names = discover_to_registry(client, reg, security)
    assert "mcp:workspace_search" in names
    assert "mcp:danger_delete" not in names

    # 经 Executor 执行：走权限链（不给权限 → 拒绝），证明不绕过 Harness
    ex = ToolExecutor(reg)
    out = ex.execute("mcp:workspace_search", {"query": "xx"}, permissions=frozenset())
    assert TOOL_PERMISSION_DENIED in out
    ok_out = ex.execute("mcp:workspace_search", {"query": "xx"},
                        permissions=frozenset({"mcp:workspace_search"}))
    assert ok_out == "hit:xx"


# ---------- 110 本地 Server：真实 stdio 子进程集成 ----------
def test_local_server_subprocess_end_to_end():
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.mcp.local_server"],
        cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, encoding="utf-8")

    def call(method, params=None, msg_id="1"):
        proc.stdin.write(p.make_request(msg_id, method, params) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline().strip()
        return p.parse(line)

    try:
        init = call(p.INITIALIZE, {"protocolVersion": "2024-11-05"})
        assert init["result"]["serverInfo"]["name"] == "agent-mvp-local"
        listed = call(p.TOOLS_LIST, None, "2")
        names = {t["name"] for t in listed["result"]["tools"]}
        assert {"workspace_search", "memory_search"} <= names

        # workspace_search 命中种子文件
        seed = os.path.join(ROOT, "workspaces", "seed_probe.md")
        with open(seed, "w", encoding="utf-8") as f:
            f.write("MCP 集成探针内容：南瓜测试关键词")
        try:
            res = call(p.TOOLS_CALL,
                       {"name": "workspace_search",
                        "arguments": {"query": "南瓜测试关键词"}}, "3")
            assert "seed_probe" in text_of(res["result"])
        finally:
            if os.path.exists(seed):
                os.remove(seed)

        # memory_search 命中种子记忆
        from src.harness.memory.long_term import LongTermStore
        store = LongTermStore(os.path.join(ROOT, "workspaces", "memory_store.json"))
        rec = store.remember("semantic", "MCP 记忆探针：蓝鲸迁徙路线")
        try:
            res = call(p.TOOLS_CALL,
                       {"name": "memory_search",
                        "arguments": {"query": "蓝鲸"}}, "4")
            assert "蓝鲸" in text_of(res["result"])
        finally:
            store.forget(rec.id)

        # 未知工具 → 错误码
        bad = call(p.TOOLS_CALL, {"name": "no_tool"}, "5")
        assert bad["error"]["code"] == -32602
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=10)
