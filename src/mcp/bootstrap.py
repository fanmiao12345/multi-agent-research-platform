# -*- coding: utf-8 -*-
"""
mcp/bootstrap.py —— D2-04 配置驱动的 MCP 接入

把"已配置的 MCP Server"接进统一工具体系：
- 配置来源：Settings.mcp_servers（环境变量 MCP_SERVERS，JSON 数组；
  每项 name/command/args/allow_tools/deny_tools/tool_risk/default_side_effect）；
- 未配置 → 返回空（明确的无操作，不静默猜测）；
- 已配置 → 按 stdio 启动子进程、initialize、tools/list 发现并注册进
  ToolRegistry（与内置工具同走 ToolExecutor 的权限/风险/超时/重试/日志链）；
- 生命周期：会话对象持有子进程，close() 终止；配置/启动失败显式报错，不静默降级。

工具名注册为 mcp:<server>:<tool>（带服务名防冲突）；未知外部操作默认
side_effect=True（不盲目自动重试），可用 tool_risk/default_side_effect 显式放开。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from src.harness.tools.registry import ToolRegistry
from src.mcp.client import MCPClient, StreamRPC
from src.mcp.security import McpSecurity

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class McpConfigError(ValueError):
    """MCP 配置无效或服务器启动失败：显式报错，不静默降级。"""


@dataclass
class McpServerSession:
    """一个已连接的 MCP Server 子进程及其注册结果；close() 负责生命周期收尾。"""
    name: str
    proc: subprocess.Popen
    registered_tools: list = field(default_factory=list)
    own_process: bool = True

    def close(self):
        if self.own_process and self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def parse_mcp_servers(raw: str) -> tuple:
    """解析 MCP_SERVERS 环境变量（JSON 数组）；空串 = 未配置。"""
    raw = (raw or "").strip()
    if not raw:
        return ()
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise McpConfigError(f"MCP_SERVERS 不是合法 JSON：{e}") from None
    if not isinstance(data, list):
        raise McpConfigError("MCP_SERVERS 必须为 JSON 数组")
    servers = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or not item.get("name") or not item.get("command"):
            raise McpConfigError(f"MCP_SERVERS[{i}] 缺少 name 或 command")
        servers.append({
            "name": str(item["name"]),
            "command": str(item["command"]),
            "args": [str(a) for a in item.get("args") or []],
            "allow_tools": set(item.get("allow_tools") or []),
            "deny_tools": set(item.get("deny_tools") or []),
            "tool_risk": dict(item.get("tool_risk") or {}),
            "default_side_effect": bool(item.get("default_side_effect", True)),
        })
    names = [s["name"] for s in servers]
    if len(names) != len(set(names)):
        raise McpConfigError("MCP_SERVERS 存在重复 name")
    return tuple(servers)


def _default_spawn(cfg: dict):
    return subprocess.Popen(
        [cfg["command"], *cfg["args"]], cwd=str(PROJECT_ROOT),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, encoding="utf-8")


def connect_configured_mcp_servers(settings, registry: ToolRegistry,
                                   *, spawn=None) -> list:
    """按配置逐个连接 MCP Server 并注册工具；未配置返回 []。返回会话列表（close 由调用方负责）。"""
    servers = getattr(settings, "mcp_servers", ()) or ()
    sessions: list = []
    spawn_fn = spawn or _default_spawn
    for cfg in servers:
        try:
            proc = spawn_fn(cfg)
        except OSError as e:
            for s in sessions:
                s.close()
            raise McpConfigError(
                f"MCP Server {cfg['name']} 启动失败：{type(e).__name__}: {e}") from None
        if proc is None:                       # 测试注入的进程句柄
            session = McpServerSession(name=cfg["name"], proc=None,
                                       own_process=False)
        else:
            session = McpServerSession(name=cfg["name"], proc=proc)

        def send(line, _p=proc):
            _p.stdin.write(line + "\n")
            _p.stdin.flush()

        def recv(_p=proc):
            return _p.stdout.readline()

        try:
            client = MCPClient(StreamRPC(send, recv), name=cfg["name"])
            security = McpSecurity(
                allow_tools=set(cfg["allow_tools"]),
                deny_tools=set(cfg["deny_tools"]),
                tool_risk=dict(cfg["tool_risk"]),
                default_side_effect=bool(cfg["default_side_effect"]))
            session.registered_tools = discover_to_registry_into(
                client, registry, security, server=cfg["name"])
        except Exception as e:
            session.close()
            for s in sessions:
                s.close()
            raise McpConfigError(
                f"MCP Server {cfg['name']} 握手/发现失败：{type(e).__name__}: {e}") from None
        sessions.append(session)
    return sessions


def discover_to_registry_into(client: MCPClient, registry: ToolRegistry,
                              security: McpSecurity, *, server: str) -> list:
    """带服务名的发现注册（mcp:<server>:<tool>），side_effect 由安全配置决定。"""
    from src.mcp.client import discover_to_registry as _legacy

    # 复用既有发现逻辑，但注册名带服务名前缀，且 side_effect 走安全配置
    names = []
    for tool in client.list_tools():
        name = tool.get("name", "")
        if not security.permits(name):
            continue
        registered = f"mcp:{server}:{name}"
        from src.harness.tools.registry import ToolSpec

        def fn(_name=name, _client=client, **kwargs):
            return _client.call_tool(_name, kwargs)

        input_schema = (tool.get("inputSchema") or {})
        registry.register(ToolSpec(
            name=registered,
            description=f"[MCP:{server}:{name}] {tool.get('description', '')}",
            func=fn,
            parameters=input_schema.get("parameters") or input_schema,
            risk_level=security.risk_of(name),
            side_effect=security.side_effect_of(name),
            timeout=30.0))
        names.append(registered)
    return names
