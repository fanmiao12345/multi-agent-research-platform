# -*- coding: utf-8 -*-
"""模型入口与只读配置诊断。真实模式失败时绝不回退为 Mock。"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from urllib.parse import urlsplit

from config.settings import Settings
from src.harness.models.profiles import get_profile
from src.llm.mock import MockLLM


class ModelConfigError(ValueError):
    """只包含固定、可安全展示的配置提示。"""


def _resolve(profile_name=None, settings=None, *, mode=None, force_mock=False):
    if not isinstance(force_mock, bool):
        raise ModelConfigError("force_mock 必须是布尔值")
    if mode not in (None, "mock", "real"):
        raise ModelConfigError("mode 必须为 mock 或 real")
    if force_mock and mode == "real":
        raise ModelConfigError("真实模式与 force_mock 冲突，请只选择一种模式")
    try:
        s = settings or Settings()
    except (ValueError, TypeError):
        raise ModelConfigError("配置数值无效，请检查 TEMPERATURE 和 MAX_TOKENS") from None
    selected = "mock" if force_mock else mode or ("mock" if s.is_mock else "real")
    profile = None
    if profile_name is not None:
        try:
            profile = get_profile(profile_name)
        except (KeyError, TypeError):
            raise ModelConfigError("未知模型档案，请选择 fast、balanced、deep 或 cheap") from None
    if selected == "mock":
        return s, selected
    if s.model_provider.lower() not in ("deepseek", "openai", "openai-compatible"):
        raise ModelConfigError("真实模式需要 MODEL_PROVIDER=deepseek、openai 或 openai-compatible")
    if not s.model_api_key.strip():
        raise ModelConfigError("MODEL_API_KEY 为空，请在项目 .env 中配置；离线演示请选择 Mock")
    if profile:
        if profile.provider != s.model_provider.lower():
            raise ModelConfigError("模型档案与供应商不匹配，请不指定档案以使用项目基础配置")
        s = replace(s, model_name=profile.model, temperature=profile.temperature,
                    max_tokens=profile.max_tokens)
    if not s.model_name.strip():
        raise ModelConfigError("MODEL_NAME 不能为空")
    try:
        url = urlsplit(s.model_base_url)
        valid_url = (url.scheme in ("http", "https") and url.hostname and
                     not url.username and not url.password and not url.query and
                     not url.fragment and not any(c.isspace() for c in s.model_base_url))
        url.port
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ModelConfigError("MODEL_BASE_URL 必须是含主机名的 HTTP(S) 地址，不能含账号、密码、查询或片段")
    if (isinstance(s.temperature, bool) or not isinstance(s.temperature, (int, float))
            or not math.isfinite(s.temperature) or not 0 <= s.temperature <= 2):
        raise ModelConfigError("TEMPERATURE 必须为 0 到 2 之间的有限数")
    if isinstance(s.max_tokens, bool) or not isinstance(s.max_tokens, int) or s.max_tokens < 1:
        raise ModelConfigError("MAX_TOKENS 必须为正整数")
    return s, selected


def diagnose_config(settings=None, *, mode=None, profile_name=None) -> dict:
    """仅检查本地配置，不实例化 SDK、不发请求、不返回密钥或完整接口地址。"""
    result = {"ready": False, "connection_tested": False, "errors": []}
    try:
        s, selected = _resolve(profile_name, settings, mode=mode)
    except ModelConfigError as e:
        result["errors"] = [str(e)]
        return result
    result.update(ready=True, mode=selected,
                  provider="mock" if selected == "mock" else s.model_provider.lower(),
                  model=MockLLM.model_name if selected == "mock" else s.model_name,
                  key_configured=bool(s.model_api_key.strip()),
                  configuration_source="profile" if profile_name and selected == "real" else "settings")
    return result


def build_adapter(profile_name: str | None = None, settings=None, *,
                  force_mock: bool = False, mode: str | None = None):
    s, selected = _resolve(profile_name, settings, mode=mode, force_mock=force_mock)
    if selected == "mock":
        return MockLLM()
    from src.llm.provider import OpenAICompatibleLLM
    return OpenAICompatibleLLM(s)


def main():
    parser = argparse.ArgumentParser(description="模型配置静态诊断（不会发送模型请求）")
    parser.add_argument("--mode", choices=("mock", "real"))
    parser.add_argument("--profile", default=None)
    args = parser.parse_args()
    result = diagnose_config(mode=args.mode, profile_name=args.profile)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ready"] else 2)


if __name__ == "__main__":
    main()
