# -*- coding: utf-8 -*-
"""测试：搜索占位网关（S2-03/04）与 Settings 搜索字段。"""
import pytest

from config.settings import Settings
from src.harness.ingest.search import (
    SearchNotConfigured, SearchRecord, check_configured, search_enabled)


def test_search_disabled_by_default_and_message_is_actionable():
    assert not search_enabled("")
    assert not search_enabled("   ")
    with pytest.raises(SearchNotConfigured) as exc:
        check_configured("", Settings())
    message = str(exc.value)
    assert "未配置" in message and "SEARCH_PROVIDER" in message


def test_unimplemented_provider_fails_not_fake():
    assert search_enabled("some-vendor")
    with pytest.raises(SearchNotConfigured, match="尚未接入"):
        check_configured("some-vendor", Settings())
    with pytest.raises(SearchNotConfigured):
        check_configured("SOME-VENDOR", Settings())  # 大小写归一仍拒绝


def test_settings_search_fields_and_secret_repr(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "demo")
    monkeypatch.setenv("SEARCH_API_KEY", "SEARCH_SECRET_KEY_XYZ")
    monkeypatch.setenv("SEARCH_BASE_URL", "https://search.example/v1")
    settings = Settings()
    assert settings.search_provider == "demo"
    assert settings.search_api_key == "SEARCH_SECRET_KEY_XYZ"
    assert "SEARCH_SECRET_KEY_XYZ" not in repr(settings)
    assert "search.example" not in repr(settings)
    monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
    assert Settings().search_provider == ""


def test_search_record_cost_unknown_by_default():
    record = SearchRecord(query="主题", provider="future-vendor")
    data = record.to_dict()
    assert data["estimated_cost_usd"] is None  # 未知成本显式保留，不记零
    assert data["cost_known"] is False
    assert data["urls"] == [] and data["error"] == ""
