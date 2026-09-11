# -*- coding: utf-8 -*-
"""Q1-01：候选版全量离线回归报告。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = ROOT / "eval" / "reports" / "q1_offline_regression.json"
OUT_MD = ROOT / "eval" / "reports" / "q1_offline_regression.md"


def run_regression() -> dict:
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    output = (proc.stdout or "") + (proc.stderr or "")
    report = {
        "schema_version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "command": f"{sys.executable} -m pytest",
        "returncode": proc.returncode,
        "passed": proc.returncode == 0,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "output_tail": output[-8000:],
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    OUT_MD.write_text(
        "# Q1-01 全量离线回归\n\n"
        f"- 时间：{report['created_at']}\n"
        f"- 命令：`{report['command']}`\n"
        f"- 结果：{'通过' if report['passed'] else '失败'}\n"
        f"- 退出码：{proc.returncode}\n"
        f"- 用时：{report['elapsed_seconds']} 秒\n\n"
        "失败不得通过跳过隐藏；原始输出尾部已保存到 JSON 报告。\n",
        encoding="utf-8")
    return report


def main():
    report = run_regression()
    print(json.dumps({k: report[k] for k in
                      ("created_at", "returncode", "passed", "elapsed_seconds")},
                     ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()