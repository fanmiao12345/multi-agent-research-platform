# -*- coding: utf-8 -*-
"""模型厂商目录、模型列表与本地配置存储。"""
import json
import urllib.error

import pytest

import config.model_config as model_config
from config.model_providers import (canonical_provider, extract_model_ids,
                                    fetch_models, list_providers,
                                    normalize_base_url)
from config.settings import load_settings


def test_provider_catalog_has_major_openai_compatible_providers():
    providers = list_providers()
    ids = {item["id"] for item in providers}
    assert {"openai", "deepseek", "dashscope", "moonshot", "zhipu",
            "siliconflow", "openrouter", "groq", "gemini", "custom"} <= ids
    assert all("api_key" not in item for item in providers)
    assert canonical_provider("openai-compatible") == "custom"


@pytest.mark.parametrize("value,expected", [
    ("https://api.example.com/v1/", "https://api.example.com/v1"),
    ("http://127.0.0.1:8000/v1", "http://127.0.0.1:8000/v1"),
    ("", ""),
])
def test_normalize_base_url(value, expected):
    assert normalize_base_url(value) == expected


@pytest.mark.parametrize("value", [
    "ftp://api.example.com/v1",
    "https://user:YOUR_PASSWORD_HERE@api.example.com/v1",
    "https://api.example.com/v1?key=YOUR_API_KEY_HERE",
    "https://api.example.com/v1#fragment",
])
def test_normalize_base_url_rejects_unsafe_urls(value):
    with pytest.raises(ValueError):
        normalize_base_url(value)


def test_extract_model_ids_handles_common_shapes():
    assert extract_model_ids({"data": [{"id": "b"}, {"id": "a"}]}) == ["a", "b"]
    assert extract_model_ids({"models": [{"name": "models/gemini-2.0-flash"}]}) == [
        "gemini-2.0-flash"]
    assert extract_model_ids(["x", "y"]) == ["x", "y"]


def test_fetch_models_uses_remote_list(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return json.dumps({"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}).encode()

    monkeypatch.setattr("config.model_providers.urllib.request.urlopen",
                        lambda request, timeout: Response())
    result = fetch_models("openai", "https://api.openai.com/v1", "YOUR_API_KEY_HERE")
    assert result["source"] == "remote"
    assert result["models"] == ["gpt-4o", "gpt-4o-mini"]
    assert "YOUR_API_KEY_HERE" not in json.dumps(result)


def test_fetch_models_falls_back_without_leaking_key(monkeypatch):
    def unauthorized(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", None, None)

    monkeypatch.setattr("config.model_providers.urllib.request.urlopen", unauthorized)
    result = fetch_models("deepseek", "https://api.deepseek.com", "YOUR_API_KEY_HERE")
    assert result["source"] == "static" and result["models"]
    assert "Key" in result["warning"]
    assert "YOUR_API_KEY_HERE" not in json.dumps(result)


def test_local_config_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "model_config.local.json"
    monkeypatch.setattr(model_config, "LOCAL_MODEL_CONFIG", path)
    model_config.save_model_config({
        "provider": "openai", "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o", "api_key": "YOUR_API_KEY_HERE"})
    loaded = model_config.load_model_config()
    assert loaded == {"provider": "openai",
                      "base_url": "https://api.openai.com/v1",
                      "model": "gpt-4o", "api_key": "YOUR_API_KEY_HERE"}
    assert path.exists()


def test_load_settings_prefers_local_and_only_reuses_env_key_for_same_provider(
        tmp_path, monkeypatch):
    path = tmp_path / "model_config.local.json"
    monkeypatch.setattr(model_config, "LOCAL_MODEL_CONFIG", path)
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("MODEL_NAME", "deepseek-chat")
    monkeypatch.setenv("MODEL_API_KEY", "ENV_KEY_HERE")
    monkeypatch.setenv("MODEL_BASE_URL", "https://api.deepseek.com")

    model_config.save_model_config({
        "provider": "openai", "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o", "api_key": "LOCAL_KEY_HERE"})
    settings = load_settings()
    assert settings.model_provider == "openai"
    assert settings.model_name == "gpt-4o"
    assert settings.model_api_key == "LOCAL_KEY_HERE"

    # 本地切到另一厂商但没有 Key 时，不能误用 deepseek 的环境 Key。
    model_config.save_model_config({
        "provider": "openai", "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o", "api_key": ""})
    assert load_settings().model_api_key == ""

    # 同一厂商可以从环境变量补齐缺失的 Key。
    model_config.save_model_config({
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "model": "deepseek-reasoner", "api_key": ""})
    assert load_settings().model_api_key == "ENV_KEY_HERE"
