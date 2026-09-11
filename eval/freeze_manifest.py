# -*- coding: utf-8 -*-
"""D10-03/04：候选版文件清单、依赖快照与功能冻结检查。"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACKER = ROOT / "docs" / "IMPLEMENTATION_TRACKER.md"
LOCK = ROOT / "requirements.lock.txt"
DATASET = ROOT / "eval" / "datasets" / "research_writing_v1.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(args: list[str]) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, encoding="utf-8", timeout=10).stdout.strip()
    except Exception:
        return ""


def build_manifest() -> dict:
    files = []
    for folder in ("src", "config", "skills", "knowledge", "scripts"):
        root = ROOT / folder
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files.append({"path": path.relative_to(ROOT).as_posix(),
                              "sha256": _sha256(path)})
    for name in ("pyproject.toml", "requirements.lock.txt"):
        path = ROOT / name
        if path.exists():
            files.append({"path": name, "sha256": _sha256(path)})
    dataset = {}
    if DATASET.exists():
        data = json.loads(DATASET.read_text(encoding="utf-8"))
        dataset = {"version": (data.get("meta") or {}).get("version", 1),
                   "tasks": len(data.get("tasks", [])),
                   "faults": len(data.get("faults", [])),
                   "sha256": _sha256(DATASET)}
    return {
        "schema_version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git(["rev-parse", "HEAD"]),
        "git_status": _git(["status", "--short"]),
        "dependencies_sha256": _sha256(LOCK) if LOCK.exists() else "",
        "dataset": dataset,
        "files": files,
        "file_count": len(files),
    }


def validate_freeze() -> dict:
    text = TRACKER.read_text(encoding="utf-8") if TRACKER.exists() else ""
    failures = []
    for line in text.splitlines():
        if line.startswith("| D") and "|" in line:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) >= 3 and cells[0].startswith("D"):
                status = cells[-1]
                if not status.startswith("功能完成"):
                    failures.append({"step": cells[0], "status": status})
    return {"ok": not failures, "failures": failures,
            "policy": "所有 D 步骤必须为功能完成；待优化项只能属于 Q 阶段"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="eval/reports/freeze_manifest.json")
    args = parser.parse_args()
    manifest = build_manifest()
    check = validate_freeze()
    manifest["freeze_check"] = check
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(out), "file_count": manifest["file_count"],
                      "freeze_check": check}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if check["ok"] else 1)


if __name__ == "__main__":
    main()