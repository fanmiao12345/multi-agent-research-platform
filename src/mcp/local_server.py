# -*- coding: utf-8 -*-
"""
mcp/local_server.py —— 本地 MCP Server（DEV_PLAN J3 / 步骤 110）

对外暴露两个能力：
    workspace_search(query)   在 workspaces/ 里搜产物文本
    memory_search(query)      搜 Long-term Store（workspaces/memory_store.json）

运行：python -m src.mcp.local_server   （stdio JSON-RPC，可被本工程 client 直连）
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from config.settings import PROJECT_ROOT
from src.mcp.server import MCPServer

WORKSPACES = PROJECT_ROOT / "workspaces"
MEMORY_STORE = WORKSPACES / "memory_store.json"


def _search_files(query: str, root: Path | None = None,
                  limit: int = 5, max_per_file: int = 3) -> str:
    hits = []
    root = Path(root) if root else WORKSPACES
    if not root.is_dir():
        return "（工作区目录不存在）"
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in (".md", ".json", ".txt"):
            continue
        if "plan.json" in path.name or path.name == "run.json":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:200_000]
        except Exception:
            continue
        if query in text:
            snippet = text.replace("\n", " ")[:200]
            hits.append(f"{path.relative_to(root)}: …{snippet}…")
            if len(hits) >= limit:
                break
    return "\n".join(hits) if hits else f"（未找到包含「{query}」的工作区产物）"


def _search_memory(query: str, limit: int = 5) -> str:
    from src.harness.memory.long_term import LongTermStore

    store = LongTermStore(MEMORY_STORE)
    recs = store.search(query, top_k=limit)
    if not recs:
        return f"（长期记忆中没有「{query}」）"
    return "\n".join(f"- [{r.kind}] {r.content}" for r in recs)


def build_local_server() -> MCPServer:
    server = MCPServer(server_name="agent-mvp-local", server_version="0.1.0")
    server.register(
        name="workspace_search",
        description="在 Agent 工作区（workspaces/）中按关键词检索历史产物文本。",
        fn=_search_files,
        input_schema={"type": "object",
                      "properties": {"query": {"type": "string"},
                                     "limit": {"type": "integer"}},
                      "required": ["query"]})
    server.register(
        name="memory_search",
        description="在 Agent 长期记忆中按关键词检索已记住的事实/经验。",
        fn=_search_memory,
        input_schema={"type": "object",
                      "properties": {"query": {"type": "string"},
                                     "limit": {"type": "integer"}},
                      "required": ["query"]})
    return server


if __name__ == "__main__":
    build_local_server().serve_stdio()
