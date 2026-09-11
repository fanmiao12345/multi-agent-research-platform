# -*- coding: utf-8 -*-
"""D8-03：恢复前审计已提交来源、阶段、子结果与未知操作。"""
from __future__ import annotations

import json
from pathlib import Path


def inspect_resume_state(job_dir: str | Path, *, state_db=None) -> dict:
    """返回可复用资产和禁止自动重放的未知操作清单。"""
    job = Path(job_dir)
    reusable_sources = []
    sources_path = job / "sources.json"
    if sources_path.exists():
        try:
            for item in json.loads(sources_path.read_text(encoding="utf-8")).get("sources", []):
                if item.get("status") in ("ok", "partial") and item.get("file_name"):
                    reusable_sources.append({
                        "source_id": item.get("source_id"),
                        "status": item.get("status"),
                        "root_source_id": item.get("root_source_id", ""),
                        "source_version": item.get("source_version", 1),
                    })
        except Exception:
            pass
    stages = sorted(path.stem for path in job.glob("stage_*.json"))
    children = []
    orchestration_path = job / "orchestration.json"
    if orchestration_path.exists():
        try:
            record = json.loads(orchestration_path.read_text(encoding="utf-8"))
            for child in record.get("subtasks", []):
                if child.get("draft_level") in ("accepted", "draft", "unable"):
                    children.append({
                        "child_job_id": child.get("child_job_id", ""),
                        "draft_level": child.get("draft_level"),
                        "result": child.get("result"),
                    })
        except Exception:
            pass
    budget = {}
    ledger_path = job / "ledger.json"
    if ledger_path.exists():
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            budget = {
                "calls": list(ledger.get("calls") or []),
                "reservations": list(ledger.get("reservations") or []),
                "estimated_cost_usd": ledger.get("estimated_cost_usd"),
                "unknown_usage_calls": ledger.get("unknown_usage_calls", 0),
            }
        except Exception:
            budget = {"error": "ledger.json unreadable"}
    unknown_operations = []
    if state_db is not None:
        try:
            rows = state_db.read(
                "SELECT op_key, action, status, error_type FROM operations"
                " WHERE job_id=? AND status='unknown' ORDER BY created_at",
                (job.name,))
            unknown_operations = [dict(row) for row in rows]
        except Exception:
            unknown_operations = [{"error": "operations unreadable"}]
    return {
        "schema_version": 1,
        "job_id": job.name,
        "reusable_sources": reusable_sources,
        "committed_stages": stages,
        "reusable_children": children,
        "budget_history": budget,
        "unknown_operations": unknown_operations,
        "policy": "只复用已提交资产；unknown 外部操作禁止自动重放",
    }