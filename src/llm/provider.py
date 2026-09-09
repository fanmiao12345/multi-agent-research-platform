# -*- coding: utf-8 -*-
"""
llm/provider.py —— 真实 LLM Adapter（DEV_PLAN A0.5 后半：先 Mock 后接真实）

OpenAI 兼容 provider（deepseek / openai / 本地网关都能用）。
使用官方 openai SDK，base_url / api_key / model 全部来自 config/settings.py。
Graph 代码不感知 Mock / Real —— 配置切换即可（A0.5 完成标准）。
"""

from __future__ import annotations

import json

from config.settings import Settings
from src.llm.base import ChatResult, LLMAdapter, ToolCall


class OpenAICompatibleLLM(LLMAdapter):
    """OpenAI 兼容接口的真实大脑。"""

    run_mode = "real"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        if not self.settings.model_api_key:
            raise RuntimeError(
                "MODEL_API_KEY 为空。请在项目根 .env 中配置（参考 .env.example），"
                "或使用 MODEL_PROVIDER=mock 离线运行。")
        import openai  # 延迟导入：Mock 模式不要求安装 openai

        self._client = openai.OpenAI(
            api_key=self.settings.model_api_key,
            base_url=self.settings.model_base_url,
            timeout=120,
            max_retries=0,  # 每次网关记录对应一次请求尝试，避免SDK内隐藏重试。
        )
        self.model_name = self.settings.model_name
        self.provider = self.settings.model_provider.lower()

    def chat(self, messages: list[dict], tools: list | None = None) -> ChatResult:
        return self.chat_limited(messages, tools)

    def chat_limited(self, messages, tools=None, *, max_tokens=None, timeout=None) -> ChatResult:
        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.settings.temperature,
            "max_tokens": min(self.settings.max_tokens, max_tokens) if max_tokens is not None else self.settings.max_tokens,
        }
        if timeout is not None:
            kwargs["timeout"] = min(120, timeout)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception:  # 不把供应商响应体、认证头或异常链带入控制台。
            raise RuntimeError("LLM 调用失败，请检查认证、模型名称、网络和服务额度") from None

        msg = resp.choices[0].message
        tool_calls = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        usage = {k: getattr(resp.usage, k) for k in ("prompt_tokens", "completion_tokens")
                 if getattr(resp.usage, k, None) is not None}
        return ChatResult(content=msg.content, tool_calls=tool_calls, usage=usage)
