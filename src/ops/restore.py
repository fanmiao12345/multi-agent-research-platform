# -*- coding: utf-8 -*-
"""D10-01：从受控备份恢复工作区。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from src.harness.state.db import verify_backup


def restore_backup(backup_dir: str | Path, workspace: str | Path, *,
                   confirm: bool = False) -> dict:
    backup = Path(backup_dir).resolve()
    target = Path(workspace).resolve()
    manifest_path = backup / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("备份缺少 manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not confirm and (target / "state.sqlite").exists():
        raise ValueError("目标工作区已有状态库；恢复会覆盖数据，请显式 confirm=True")
    restored = []
    state_backup = backup / "state.sqlite"
    if state_backup.exists():
        if not verify_backup(state_backup):
            raise ValueError("备份中的 state.sqlite 校验失败")
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(state_backup, target / "state.sqlite")
        restored.append({"name": "state.sqlite", "verify": True})
    for name in ("jobs", "sessions", "runs"):
        source = backup / name
        if source.is_dir():
            destination = target / name
            shutil.copytree(source, destination, dirs_exist_ok=True)
            restored.append({"name": name,
                             "files": sum(1 for p in destination.rglob("*") if p.is_file())})
    return {"workspace": str(target), "backup_dir": str(backup),
            "restored": restored, "manifest": manifest}