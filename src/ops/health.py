# -*- coding: utf-8 -*-
"""
ops/health.py —— 项目内健康检查（S6-08）

只读检查并输出清单；不发送模型请求、不改任何文件。
运行：python -m src.ops.health [--workspace 路径]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from config.settings import PROJECT_ROOT, Settings


def run_checks(workspace_root: str | Path | None = None,
               settings: Settings | None = None) -> dict:
    settings = settings or Settings()
    root = Path(workspace_root) if workspace_root else settings.workspace_dir
    checks = []

    def add(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("python 版本", sys.version_info >= (3, 10),
        f"{sys.version.split()[0]}（项目开发于 3.14）")
    env_file = PROJECT_ROOT / ".env"
    add("环境文件", env_file.exists() or bool(os.environ.get("MODEL_PROVIDER")),
        "存在 .env 或已注入环境变量（不读取其中密钥）" if (env_file.exists() or os.environ.get("MODEL_PROVIDER")) else "未发现 .env；真实模式将不可用")
    try:
        from src.harness.models.factory import diagnose_config
        report = diagnose_config(settings, mode="real", profile_name=None)
        add("真实模式配置", bool(report.get("ready")),
            "；".join(report.get("errors", []))[:200] if not report.get("ready") else "ready（未连接验证）")
    except Exception as e:  # noqa: BLE001
        add("真实模式配置", False, f"{type(e).__name__}")
    workspaces = Path(root)
    try:
        workspaces.mkdir(parents=True, exist_ok=True)
        add("工作区可写", True, str(workspaces))
    except OSError as e:
        add("工作区可写", False, str(e))
    state_db = workspaces / "state.sqlite"
    if state_db.exists():
        try:
            from src.harness.state.db import StateDb
            handle = StateDb(state_db)
            count = handle.read("SELECT COUNT(*) AS n FROM jobs")[0]["n"]
            handle.close()
            add("状态库(SQLite)", True, f"{state_db.name} 可打开；任务行 {count}")
        except Exception as e:  # noqa: BLE001
            add("状态库(SQLite)", False, f"{type(e).__name__}: {e}")
    else:
        add("状态库(SQLite)", True, "尚未创建（首次研究任务提交时自动建立）")
    samples = PROJECT_ROOT / "eval" / "datasets" / "research_writing_v1.json"
    if samples.exists():
        try:
            dataset = json.loads(samples.read_text(encoding="utf-8"))
            version = (dataset.get("meta") or {}).get("version", 1)
            add("业务数据集", True,
                f"{len(dataset.get('tasks', []))}业务+{len(dataset.get('faults', []))}故障"
                f"定义可用（数据集 v{version}；历史批次冻结分母见 meta.v1_baseline）")
        except Exception as e:  # noqa: BLE001
            add("业务数据集", False, f"读取失败：{type(e).__name__}: {e}")
    else:
        add("业务数据集", False, "缺失")
    import importlib.metadata
    required = ["langgraph", "langchain-core", "openai", "python-dotenv", "pypdf"]
    for package in required:
        try:
            version = importlib.metadata.version(package)
            add(f"依赖 {package}", True, version)
        except Exception:
            add(f"依赖 {package}", False, "未安装")
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=None)
    args = parser.parse_args()
    result = run_checks(args.workspace)
    for check in result["checks"]:
        print(f"[{'OK' if check['ok'] else 'FAIL'}] {check['name']}：{check['detail']}")
    print(json.dumps({"ok": result["ok"]}))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
