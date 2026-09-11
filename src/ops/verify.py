# -*- coding: utf-8 -*-
"""
ops/verify.py —— 全新环境验证清单（S6-07/08）

在干净虚拟环境里按文档安装后运行：导入冒烟、离线 CLI 样例、配置诊断、
健康检查；任何一步失败都给出可操作提示（不回写全局环境）。
运行：python -m src.ops.verify
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from config.settings import PROJECT_ROOT


def run_verify(*, python: str | None = None) -> dict:
    python = python or sys.executable
    checks = []

    def step(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": ok, "detail": detail})

    # 1) 导入冒烟
    modules = ["src.llm.mock", "src.graph.agent_loop", "src.harness.state.queue",
               "src.application.pipeline.runner", "src.harness.ingest.fetcher",
               "src.interfaces.web.workbench", "eval.research_cases"]
    try:
        for module in modules:
            __import__(module)
        step("导入冒烟", True, f"{len(modules)} 个核心模块")
    except Exception as e:  # noqa: BLE001
        step("导入冒烟", False, f"{type(e).__name__}: {e}")
    # 2) 离线 CLI 样例（默认 Mock，不应失败）
    with tempfile.TemporaryDirectory() as tmp:
        # 继承完整环境（Windows 上 asyncio 初始化需要 SYSTEMROOT，精简环境会崩），
        # 只强制 UTF-8 输出；Mock 样例不读取密钥。
        import os
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [python, "-m", "src.interfaces.cli", "计算6*7", "--workspace", tmp],
            capture_output=True, text=True, encoding="utf-8",
            env=env, timeout=60)
        ok = proc.returncode == 0 and "42" in proc.stdout
        step("离线 CLI 样例", ok, proc.stdout.strip()[:120] if not ok else "计算6*7→42")
    # 3) 配置诊断
    try:
        from src.harness.models.factory import diagnose_config
        from config.settings import Settings
        report = diagnose_config(Settings(), mode="mock", profile_name=None)
        step("配置诊断(mock)", bool(report.get("ready")),
             "；".join(report.get("errors", []))[:200])
    except Exception as e:  # noqa: BLE001
        step("配置诊断(mock)", False, f"{type(e).__name__}")
    # 4) 健康检查
    try:
        from src.ops.health import run_checks
        health = run_checks(Settings().workspace_dir)
        step("健康检查", health["ok"],
             f"{sum(1 for c in health['checks'] if c['ok'])}/{len(health['checks'])} 项通过")
    except Exception as e:  # noqa: BLE001
        step("健康检查", False, f"{type(e).__name__}")
    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "python": sys.version.split()[0],
            "project_root": str(PROJECT_ROOT)}


def main() -> None:
    result = run_verify()
    for check in result["checks"]:
        print(f"[{'✓' if check['ok'] else '✗'}] {check['name']}：{check['detail']}")
    print(json.dumps({"ok": result["ok"]}))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
