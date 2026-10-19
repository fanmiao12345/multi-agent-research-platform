# -*- coding: utf-8 -*-
"""测试隔离：不读取用户真实的本地模型配置或 Key。"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_local_model_config(tmp_path, monkeypatch):
    import config.model_config as model_config
    path = tmp_path / "model_config.local.json"
    monkeypatch.setattr(model_config, "LOCAL_MODEL_CONFIG", path)
    # 子进程（CLI 用例）也继承这个覆盖，避免读到项目里的真实 Key 文件。
    monkeypatch.setenv("MODEL_CONFIG_PATH", str(path))
