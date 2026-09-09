# -*- coding: utf-8 -*-
"""
ops/backup.py —— 项目内备份/恢复助手（S6-08）

- 备份 = state.sqlite 在线备份（backup API）+ jobs/ 与 sessions/ 目录复制到
  时间戳目录，生成 manifest.json（校验可读）；
- 恢复说明：停掉服务后把内容复制回工作区即可；本工具只做备份与校验，不自动覆盖
  正在运行的工作区（避免“恢复时正在写”损坏）。
运行：python -m src.ops.backup --workspace workspaces --out <备份父目录>
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from src.harness.state.db import StateDb, verify_backup
from src.harness.run_store import write_json


def backup_workspace(workspace_root: str | Path, out_dir: str | Path) -> dict:
    workspace = Path(workspace_root)
    destination = Path(out_dir) / ("backup_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    destination.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"created_at": datetime.now().isoformat(timespec="seconds"),
                      "workspace": str(workspace), "parts": []}
    state_db = workspace / "state.sqlite"
    if state_db.exists():
        handle = StateDb(state_db)
        try:
            backup_path = handle.backup_to(destination / "state.sqlite")
        finally:
            handle.close()
        manifest["parts"].append({"name": "state.sqlite",
                                  "verify": verify_backup(backup_path)})
    for folder in ("jobs", "sessions", "runs"):
        source = workspace / folder
        if source.is_dir():
            target = destination / folder
            shutil.copytree(source, target,
                            ignore=shutil.ignore_patterns("*.tmp"))
            count = sum(1 for _ in target.rglob("*") if _.is_file())
            manifest["parts"].append({"name": folder, "files": count})
    manifest_path = destination / "manifest.json"
    write_json(manifest_path, manifest)
    return {"backup_dir": str(destination), "manifest": manifest}


def main() -> None:
    import argparse
    from config.settings import Settings
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    root = args.workspace or str(Settings().workspace_dir)
    result = backup_workspace(root, args.out)
    print(json.dumps({"backup_dir": result["backup_dir"],
                      "parts": result["manifest"]["parts"]},
                     ensure_ascii=False, indent=2))
    print("恢复：先停止服务，再把备份目录内容复制回工作区；不要对正在运行的工作区覆盖。")


if __name__ == "__main__":
    main()
