# -*- coding: utf-8 -*-
"""模型厂商目录、OpenAI 兼容地址与模型列表拉取。

所有厂商都走 OpenAI 兼容协议（/chat/completions 与 /models）。
没有列入目录的接口可使用 custom，由用户填写 base URL、模型名和 Key。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit


PROVIDERS: tuple[dict, ...] = (
    {
        "id": "openai", "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "o3-mini"],
        "docs": "https://platform.openai.com/docs/models",
        "note": "OpenAI 官方 API，OpenAI 兼容协议。",
    },
    {
        "id": "deepseek", "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "docs": "https://api-docs.deepseek.com/",
        "note": "DeepSeek 官方 API，OpenAI 兼容协议。",
    },
    {
        "id": "dashscope", "label": "阿里云百炼 / 通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-max", "qwen-plus", "qwen-turbo"],
        "docs": "https://help.aliyun.com/zh/model-studio/",
        "note": "使用百炼的 OpenAI 兼容模式；模型列表接口不可用时可手动填写。",
    },
    {
        "id": "moonshot", "label": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "docs": "https://platform.moonshot.cn/docs",
        "note": "Moonshot 官方 API，OpenAI 兼容协议。",
    },
    {
        "id": "zhipu", "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-5.3", "glm-5.2", "glm-5.3-flash"],
        "docs": "https://open.bigmodel.cn/dev/api",
        "note": "智谱开放平台，OpenAI 兼容协议；列表可能受账号权限/接口版本影响，也可手动填写最新模型 ID。",
    },
    {
        "id": "siliconflow", "label": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct"],
        "docs": "https://docs.siliconflow.cn/",
        "note": "聚合多家开源模型，OpenAI 兼容协议。",
    },
    {
        "id": "openrouter", "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["openai/gpt-4o", "anthropic/claude-3.5-sonnet",
                   "google/gemini-2.0-flash-001"],
        "docs": "https://openrouter.ai/docs",
        "note": "一个 Key 访问多家模型；实际可用模型以列表接口返回为准。",
    },
    {
        "id": "groq", "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        "docs": "https://console.groq.com/docs",
        "note": "Groq 官方 API，OpenAI 兼容协议。",
    },
    {
        "id": "together", "label": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "models": ["meta-llama/Llama-3.3-70B-Instruct-Turbo"],
        "docs": "https://docs.together.ai/",
        "note": "Together 官方 API，OpenAI 兼容协议。",
    },
    {
        "id": "xai", "label": "xAI Grok",
        "base_url": "https://api.x.ai/v1",
        "models": ["grok-2-latest", "grok-beta"],
        "docs": "https://docs.x.ai/",
        "note": "xAI 官方 API，OpenAI 兼容协议。",
    },
    {
        "id": "gemini", "label": "Google Gemini（OpenAI 兼容）",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": ["gemini-2.0-flash", "gemini-2.5-pro"],
        "docs": "https://ai.google.dev/gemini-api/docs/openai",
        "note": "使用 Gemini 的 OpenAI 兼容入口；模型列表不可用时可手动填写。",
    },
    {
        "id": "mistral", "label": "Mistral AI",
        "base_url": "https://api.mistral.ai/v1",
        "models": ["mistral-large-latest", "mistral-small-latest"],
        "docs": "https://docs.mistral.ai/",
        "note": "Mistral 官方 API，OpenAI 兼容协议。",
    },
    {
        "id": "custom", "label": "自定义（OpenAI 兼容）",
        "base_url": "", "models": [],
        "docs": "",
        "note": "填写任何 OpenAI 兼容服务的 base URL、模型名和 Key。",
    },
)

_BY_ID = {item["id"]: item for item in PROVIDERS}
_ALIASES = {"openai-compatible": "custom"}


def canonical_provider(provider_id: str) -> str:
    value = (provider_id or "").strip().lower()
    return _ALIASES.get(value, value)


def get_provider(provider_id: str) -> dict | None:
    return _BY_ID.get(canonical_provider(provider_id))


def is_supported_provider(provider_id: str) -> bool:
    return get_provider(provider_id) is not None


def list_providers() -> list[dict]:
    """返回可安全展示的厂商目录（不含任何密钥）。"""
    return [{**item, "models": list(item["models"])} for item in PROVIDERS]


def normalize_base_url(value: str) -> str:
    """校验并规范化 OpenAI 兼容 base URL；空值返回空串。"""
    url = (value or "").strip().rstrip("/")
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        valid = (parts.scheme in ("http", "https") and parts.hostname
                 and not parts.username and not parts.password
                 and not parts.query and not parts.fragment
                 and not any(ch.isspace() for ch in url))
        parts.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "接口地址必须是含主机名的 HTTP(S) 地址，不能包含账号、密码、查询参数或片段")
    return url


def extract_model_ids(payload) -> list[str]:
    """兼容 OpenAI /models、models 列表和纯字符串列表。"""
    if isinstance(payload, dict):
        items = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            value = item.get("id") or item.get("name") or item.get("model")
        else:
            value = item
        if not isinstance(value, str):
            continue
        value = value.strip()
        if value.startswith("models/"):
            value = value.split("/", 1)[1]
        if value:
            out.append(value)
    return sorted(dict.fromkeys(out))


def fetch_models(provider_id: str, base_url: str, api_key: str, *,
                 timeout: float = 15.0) -> dict:
    """尽力从厂商的 /models 拉取列表；失败时回退到目录里的推荐模型。

    返回结构固定为 {models, source, warning}，不包含 Key 或厂商响应正文。
    """
    provider = get_provider(provider_id)
    if provider is None:
        return {"models": [], "source": "static", "warning": "未知厂商"}
    static = list(provider["models"])
    try:
        base = normalize_base_url(base_url or provider["base_url"])
    except ValueError as exc:
        return {"models": static, "source": "static", "warning": str(exc)}
    if not base:
        return {"models": static, "source": "static",
                "warning": "请填写接口地址后再获取模型列表"}
    key = (api_key or "").strip()
    if not key:
        return {"models": static, "source": "static",
                "warning": "未填写 API Key，已显示推荐模型；填写后可在线获取"}

    request = urllib.request.Request(
        base + "/models",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "agent-mvp-model-config/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_000)
        payload = json.loads(raw.decode("utf-8"))
        models = extract_model_ids(payload)
        if models:
            return {"models": models[:500], "source": "remote", "warning": ""}
        return {"models": static, "source": "static",
                "warning": "厂商未返回模型列表，已显示推荐模型；也可手动填写模型名"}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            warning = "API Key 可能无效或无权限，已显示推荐模型"
        else:
            warning = f"厂商模型列表接口返回 HTTP {exc.code}，已显示推荐模型"
        return {"models": static, "source": "static", "warning": warning}
    except Exception:  # noqa: BLE001 —— 不把供应商响应体/认证头带入界面
        return {"models": static, "source": "static",
                "warning": "无法连接厂商模型列表接口，已显示推荐模型"}
