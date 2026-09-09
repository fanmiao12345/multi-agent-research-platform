# -*- coding: utf-8 -*-
"""
config/settings.py —— 配置系统（DEV_PLAN A0.3）

原则：
- 配置不散落在源码里：统一从环境变量读取（可经由项目根 .env 注入）；
- API Key 只从环境读取；.env.example 只放字段名；
- MODEL_PROVIDER=mock 时可以在没有 API Key 的情况下离线运行。

支持的键（详见根目录 .env.example）：
MODEL_PROVIDER / MODEL_NAME / MODEL_API_KEY / MODEL_BASE_URL /
TEMPERATURE / MAX_TOKENS / TRACE_LEVEL / WORKSPACE_DIR
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv 尚未安装时也能工作（纯环境变量模式）
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(key: str, default: str) -> str:
    return os.environ.get(key, "").strip() or default


def _number(key: str, default: str, convert):
    try:
        return convert(_env(key, default))
    except ValueError:
        raise ValueError(f"{key} 必须是有效数字，请检查项目配置") from None


@dataclass(frozen=True)
class Settings:
    """一次进程的静态配置。Runtime Context 会引用它，但不再重复造轮子。"""

    model_provider: str = field(default_factory=lambda: _env("MODEL_PROVIDER", "deepseek"))
    model_name: str = field(default_factory=lambda: _env("MODEL_NAME", "deepseek-chat"))
    model_api_key: str = field(default_factory=lambda: _env("MODEL_API_KEY", ""), repr=False)
    model_base_url: str = field(default_factory=lambda: _env("MODEL_BASE_URL", "https://api.deepseek.com"), repr=False)
    temperature: float = field(default_factory=lambda: _number("TEMPERATURE", "0.7", float))
    max_tokens: int = field(default_factory=lambda: _number("MAX_TOKENS", "2048", int))
    trace_level: str = field(default_factory=lambda: _env("TRACE_LEVEL", "INFO").upper())
    workspace_dir: Path = field(
        default_factory=lambda: PROJECT_ROOT / _env("WORKSPACE_DIR", "workspaces"))
    # B4 搜索网关（S2-03/04）：未配置即禁用；配置后由实施按官方接口核验再接入
    search_provider: str = field(default_factory=lambda: _env("SEARCH_PROVIDER", ""))
    search_api_key: str = field(default_factory=lambda: _env("SEARCH_API_KEY", ""), repr=False)
    search_base_url: str = field(default_factory=lambda: _env("SEARCH_BASE_URL", ""), repr=False)
    search_max_results: int = field(default_factory=lambda: _number("SEARCH_MAX_RESULTS", "5", int))

    @property
    def is_mock(self) -> bool:
        return self.model_provider.lower() in ("mock", "local-mock")


def get_settings() -> Settings:
    return Settings()
