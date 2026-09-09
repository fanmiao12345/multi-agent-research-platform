# -*- coding: utf-8 -*-
"""
llm/mock.py —— MockLLM Adapter（DEV_PLAN A0.5 / D-003 Mock First）

没有 API Key 也能驱动整个 Harness：用确定性关键词规则“假装思考”。
规则 = 一个极简模型：命中规则就请求工具，否则返回文本。
Unit Test 因此不依赖真实模型与 token。
"""

from __future__ import annotations

import re

from src.llm.base import ChatResult, LLMAdapter, ToolCall

_TIME_WORDS = ("几点了", "几点", "时间", "日期", "今天星期")
_WEB_WORDS = ("搜索", "查资料", "调研", "联网", "检索", "查一下")


class MockLLM(LLMAdapter):
    """确定性 Mock 大脑。model_name 固定便于 Trace 识别。"""

    model_name = "mock-rule-v1"
    run_mode = "mock"
    provider = "mock"

    def chat(self, messages: list[dict], tools: list | None = None) -> ChatResult:
        # 0) 上一条是工具结果 -> 扮演"看完结果收尾"的模型（与规则触发同等重要）
        if messages and messages[-1].get("role") == "tool":
            results = []
            for m in reversed(messages):
                if m.get("role") == "tool":
                    results.append(m["content"])
                elif m.get("tool_calls"):
                    break
            summary = "\n".join(f"- {r}" for r in reversed(results))
            return ChatResult(content=f"工具已返回内容：\n{summary}")

        # 最近一条用户消息是规则判断的输入
        text = ""
        for m in reversed(messages):
            if m.get("role") == "user" and m.get("content"):
                text = m["content"]
                break
        text = (text or "").replace("，", " ").replace("？", " ")

        # 长文本保护：>200 字不做逐词路由（模拟"长输入交给真实模型"的语义，
        # 避免长文里的零散关键词误触发工具）
        if len(text) > 200:
            return ChatResult(content="（模拟大脑）这是一段较长的输入，离线模式不做逐词路由。"
                                      "配置 API Key 后由真实模型处理。")

        def tool(name: str, arguments: dict) -> ChatResult:
            return ChatResult(tool_calls=[ToolCall(id="mock_1", name=name,
                                                   arguments=arguments)])

        if any(k in text for k in _TIME_WORDS):
            return tool("current_time", {})
        expr = self._find_expression(text)
        if expr:
            return tool("calculator", {"expression": expr})
        if any(k in text for k in _WEB_WORDS) and tools:  # 未注册工具时不要乱请求
            return tool("web_search", {"query": text[:40] or "mock"})
        return ChatResult(content=f"（mock）收到：{text[:80] or '（空）'}")

    @staticmethod
    def _find_expression(text: str) -> str | None:
        """取出含数字与运算符的最长片段（含中文算符翻译）。"""
        for zh, en in (("乘以", "*"), ("除以", "/"), ("加上", "+"), ("减去", "-")):
            text = text.replace(zh, en)
        candidates = re.findall(r"[0-9+\-*/%.()\s]+", text)
        candidates = [c.strip() for c in candidates
                      if c.strip() and any(ch.isdigit() for ch in c) and len(c.strip()) <= 30]
        if candidates and any(k in text for k in ("计算", "算", "等于", "多少")):
            return max(candidates, key=len)
        return None
