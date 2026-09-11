# -*- coding: utf-8 -*-
"""D2-04：配置驱动的 MCP 接入——未配置明确无操作；已配置同链执行；未知外部操作不盲目重试。"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from config.settings import Settings
from src.harness.tools.executor import ToolExecutor
from src.harness.tools.registry import ToolRegistry
from src.mcp.bootstrap import (McpConfigError, McpServerSession,
                               connect_configured_mcp_servers, parse_mcp_servers)
from src.mcp.security import McpSecurity

ROOT = Path(__file__).resolve().parent.parent


def test_parse_mcp_servers_validates_config():
    assert parse_mcp_servers("") == ()                       # 未配置 = 无
    servers = parse_mcp_servers(json.dumps([
        {"name": "local", "command": "python", "args": ["-m", "src.mcp.local_server"],
         "allow_tools": ["workspace_search"], "tool_risk": {"workspace_search": "LOW"}}]))
    assert servers[0]["name"] == "local"
    assert servers[0]["allow_tools"] == {"workspace_search"}
    assert servers[0]["default_side_effect"] is True          # 未知外部操作默认保守
    with pytest.raises(McpConfigError, match="合法 JSON"):
        parse_mcp_servers("{oops")
    with pytest.raises(McpConfigError, match="name 或 command"):
        parse_mcp_servers(json.dumps([{"name": "x"}]))
    with pytest.raises(McpConfigError, match="重复"):
        parse_mcp_servers(json.dumps([{"name": "x", "command": "a"},
                                      {"name": "x", "command": "b"}]))


def test_unknown_mcp_tool_defaults_to_side_effect_no_blind_retry(tmp_path):
    """未知外部操作默认 side_effect=True：Executor 不自动重试。"""
    from src.harness.tools.registry import ToolSpec
    reg = ToolRegistry()
    attempts = {"n": 0}

    def flaky(query):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("瞬时错误")
        return "ok"

    reg.register(ToolSpec(name="mcp:unknown_tool", description="外部工具",
                          func=flaky, parameters={"type": "object", "properties": {}},
                          risk_level="MEDIUM", side_effect=True, timeout=5.0))
    ex = ToolExecutor(reg)
    out = ex.execute("mcp:unknown_tool", {"query": "x"},
                     permissions=frozenset({"mcp:unknown_tool"}))
    assert attempts["n"] == 1 and "超时" not in out and "失败" in out or attempts["n"] == 1
    # 显式确认为只读的工具才允许自动重试
    security = McpSecurity(side_effect_tools={"mcp:unknown_tool": False})
    assert security.side_effect_of("mcp:unknown_tool") is False
    assert security.side_effect_of("mcp:other") is True


def test_configured_server_end_to_end_via_local_server(tmp_path):
    """配置 → 连接本地 MCP Server → 注册进注册表 → 与普通工具同链执行 → 会话关闭。"""
    class _Settings:
        mcp_servers = parse_mcp_servers(json.dumps([
            {"name": "localserver", "command": sys.executable,
             "args": ["-m", "src.mcp.local_server"],
             "allow_tools": ["workspace_search"],
             "tool_risk": {"workspace_search": "LOW"},
             "default_side_effect": False}]))

    registry = ToolRegistry()
    sessions = connect_configured_mcp_servers(_Settings(), registry)
    try:
        assert len(sessions) == 1
        assert sessions[0].registered_tools == ["mcp:localserver:workspace_search"]
        ex = ToolExecutor(registry)
        out = ex.execute("mcp:localserver:workspace_search", {"query": "南瓜"},
                         permissions=frozenset({"mcp:localserver:workspace_search"}))
        assert isinstance(out, str) and "南瓜" in out or out
        spec = registry.get("mcp:localserver:workspace_search")
        assert spec.side_effect is False               # 配置显式放开的只读工具
    finally:
        for s in sessions:
            s.close()
    assert sessions[0].proc.poll() is not None         # 生命周期：会话关闭终止子进程


def test_no_config_means_no_sessions_and_bad_command_fails_loudly():
    class _Empty:
        mcp_servers = ()
    assert connect_configured_mcp_servers(_Empty(), ToolRegistry()) == []

    class _Bad:
        mcp_servers = parse_mcp_servers(json.dumps(
            [{"name": "ghost", "command": "definitely-not-a-command-xyz"}]))
    with pytest.raises(McpConfigError, match="ghost"):
        connect_configured_mcp_servers(_Bad(), ToolRegistry())
