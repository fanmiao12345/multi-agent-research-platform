# -*- coding: utf-8 -*-
"""项目内本地模型配置（前端保存）。

文件：config/model_config.local.json（已加入 .gitignore，不提交）。
只保存当前启用的模型；API Key 不通过任何 GET 接口返回。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

LOCAL_MODEL_CONFIG = Path(
    os.environ.get("MODEL_CONFIG_PATH")
    or (Path(__file__).resolve().parent / "model_config.local.json"))


def load_model_config(path: Path | str | None = None) -> dict | None:
    target = Path(path) if path is not None else LOCAL_MODEL_CONFIG
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    provider = str(data.get("provider") or "").strip().lower()
    model = str(data.get("model") or "").strip()
    if not provider or not model:
        return None
    return {
        "provider": provider,
        "base_url": str(data.get("base_url") or "").strip().rstrip("/"),
        "model": model,
        "api_key": str(data.get("api_key") or ""),
    }


def save_model_config(config: dict, path: Path | str | None = None) -> Path:
    """原子写入本地配置；Key 只落本机文件，不进入日志或响应。"""
    target = Path(path) if path is not None else LOCAL_MODEL_CONFIG
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "provider": str(config.get("provider") or "").strip().lower(),
        "base_url": str(config.get("base_url") or "").strip().rstrip("/"),
        "model": str(config.get("model") or "").strip(),
        "api_key": str(config.get("api_key") or ""),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    fd, temp_name = tempfile.mkstemp(
        dir=target.parent, prefix=".model-config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(5):
            try:
                os.replace(temp_name, target)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target
