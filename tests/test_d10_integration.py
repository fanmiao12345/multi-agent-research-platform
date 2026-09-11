# -*- coding: utf-8 -*-
"""D10：安装恢复、MCP、冻结清单与候选版工具。"""
from pathlib import Path

from eval.freeze_manifest import build_manifest
from src.harness.state.db import StateDb
from src.mcp.local_server import build_local_server
from src.ops.backup import backup_workspace
from src.ops.restore import restore_backup

ROOT = Path(__file__).resolve().parent.parent


def test_backup_and_restore_roundtrip(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = StateDb(workspace / "state.sqlite")
    db.close()
    (workspace / "jobs" / "job_x").mkdir(parents=True)
    (workspace / "jobs" / "job_x" / "job.json").write_text("{}", encoding="utf-8")
    result = backup_workspace(workspace, tmp_path / "backups")
    restored = restore_backup(result["backup_dir"], tmp_path / "restored")
    assert (tmp_path / "restored" / "state.sqlite").exists()
    assert (tmp_path / "restored" / "jobs" / "job_x" / "job.json").exists()
    assert restored["restored"]


def test_local_mcp_server_exposes_read_only_tools():
    server = build_local_server()
    names = {tool["name"] for tool in server.list_tools()}
    assert names == {"workspace_search", "memory_search"}


def test_freeze_manifest_hashes_sources_and_dependencies():
    manifest = build_manifest()
    assert manifest["file_count"] > 20
    assert manifest["dependencies_sha256"]
    assert any(item["path"] == "src/application/orchestration/executor.py"
               for item in manifest["files"])


def test_install_and_mcp_docs_exist():
    assert (ROOT / "docs" / "INSTALL_AND_RECOVERY.md").exists()
    assert (ROOT / "docs" / "MCP_USAGE.md").exists()
    assert (ROOT / "docs" / "FEATURE_FREEZE.md").exists()
    assert (ROOT / "scripts" / "install.ps1").exists()
    assert (ROOT / "scripts" / "restore.ps1").exists()