# -*- coding: utf-8 -*-
"""
llm/base.py —— 统一 LLM 接口（DEV_PLAN A0.5 / D-001）

同一份 Graph/Agent 代码，通过配置在 Mock 与真实模型之间切换，
Graph 结构本身不做任何修改 —— 这是“Adapter”存在的意义。

约定（贯穿整个 Harness）：
- messages：OpenAI 风格消息列表 [{"role": ..., "content": ...}, ...]
- tools：OpenAI function calling 的 tools 参数（可空）
- 返回 ChatResult：content 与 tool_calls 二选一（tool_calls 非空 = 请求调用工具）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """模型发出的一次工具调用请求（参数已解析为 dict）。"""
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class ChatResult:
    """一次 chat 调用的统一返回。"""
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)  # {"prompt_tokens":..,"completion_tokens":..}

    @property
    def wants_tool(self) -> bool:
        return bool(self.tool_calls)


class LLMAdapter(ABC):
    """所有大脑（Mock / Real）实现同一接口。"""

    model_name: str = ""

    @abstractmethod
    def chat(self, messages: list[dict], tools: list | None = None) -> ChatResult:
        """发一次请求。tools 为空表示这一轮不允许调用工具。"""
        raise NotImplementedError
