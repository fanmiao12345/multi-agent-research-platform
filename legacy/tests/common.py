# -*- coding: utf-8 -*-
"""测试公共工具：联网工具假实现（测试不碰真网络、不花 token）。"""
import contextlib
import os
import shutil

import tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@contextlib.contextmanager
def fake_web_tools():
    """把 web_search / fetch_page 换成确定性假实现，用完恢复。"""
    originals = {k: tools.TOOL_REGISTRY[k]
                 for k in ("web_search", "fetch_page")}
    tools.TOOL_REGISTRY["web_search"] = (
        lambda query: f"（测试资料 {query}）要点一；要点二；来源 https://example.test/1")
    tools.TOOL_REGISTRY["fetch_page"] = (
        lambda url, max_chars=3000: f"（测试正文）{url} 的内容片段")
    try:
        yield
    finally:
        tools.TOOL_REGISTRY.update(originals)


def cleanup_research_output():
    """删除 research_output 下测试产生的运行目录，保持仓库整洁。"""
    out = os.path.join(ROOT, "research_output")
    if os.path.isdir(out):
        shutil.rmtree(out, ignore_errors=True)
