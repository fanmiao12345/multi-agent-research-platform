# -*- coding: utf-8 -*-
"""
harness/run_store.py —— Run Workspace 第一版（DEV_PLAN A0.8 / 步骤 15）

每次正式运行创建 workspaces/<run_id>/，至少保存 run.json。
run_id：时间戳 + 短随机串；同名目录不会冲突。

run.json 字段（按文档）：
run_id / started_at / finished_at / status / input / model
"""

from __future__ import annotations

import json
import time
import uuid
import os
import tempfile
from pathlib import Path

from config.settings import Settings

RUN_STATUSES = ("running", "waiting_human", "completed", "failed", "cancelled")


def new_run_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]


def start_run(workspace_root: Path | None = None,
              user_input: str = "", model: str = "") -> dict:
    """创建一次运行目录并写 run.json（status=running）。返回 run 描述。"""
    root = workspace_root or Settings().workspace_dir
    root.mkdir(parents=True, exist_ok=True)
    run = {
        "run_id": new_run_id(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "finished_at": None,
        "status": "running",
        "input": user_input,
        "model": model,
    }
    run_dir = root / run["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_run(run_dir, run)
    run["dir"] = str(run_dir)
    return run


def finish_run(run: dict, status: str) -> dict:
    """把一次运行标记为终态（completed/failed/cancelled）。"""
    if status not in ("completed", "failed", "cancelled"):
        raise ValueError(f"终态必须为 {RUN_STATUSES}")
    run["status"] = status
    run["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _write_run(Path(run["dir"]), run)
    return run


def update_run(run: dict, **fields) -> None:
    """持久化当前状态及最终回答等运行元数据。"""
    run.update(fields)
    _write_run(Path(run["dir"]), run)


def _write_run(run_dir: Path, run: dict) -> None:
    write_json(run_dir / "run.json", {k: v for k, v in run.items() if k != "dir"})


def write_json(path: Path, payload) -> None:
    """同目录原子替换；兼容 Windows 读者/索引器短暂占用文件。"""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".json-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.01 * 2 ** attempt)
    finally:
        Path(tmp).unlink(missing_ok=True)
