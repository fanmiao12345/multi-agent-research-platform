# -*- coding: utf-8 -*-
"""
harness/state/sessions.py —— 会话与材料归属（S4-05）

- sessions 表存会话目标；session_jobs 记录该会话下的任务；
- 默认不跨会话共享资料：每个会话的材料快照（workspaces/sessions/<sid>/materials.json）
  只登记本会话任务自己的来源与产物；加载续写上下文时绝不串入其他会话资料。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from src.harness.run_store import write_json
from src.harness.state.db import StateDb


class SessionStore:
    def __init__(self, db: StateDb):
        self.db = db

    def create(self, goal: str) -> str:
        session_id = "session_" + uuid.uuid4().hex[:12]
        with self.db.write_tx() as conn:
            conn.execute(
                "INSERT INTO sessions(session_id, goal, created_at) VALUES(?,?,?)",
                (session_id, goal, self.db.now()))
        return session_id

    def attach_job(self, session_id: str, job_id: str) -> None:
        with self.db.write_tx() as conn:
            conn.execute(
                "UPDATE jobs SET session_id=? WHERE job_id=? AND session_id=''",
                (session_id, job_id))
            conn.execute(
                "INSERT OR IGNORE INTO session_jobs(session_id, job_id, created_at)"
                " VALUES(?,?,?)", (session_id, job_id, self.db.now()))

    def goal(self, session_id: str) -> str:
        rows = self.db.read(
            "SELECT goal FROM sessions WHERE session_id=?", (session_id,))
        return rows[0]["goal"] if rows else ""

    def job_ids(self, session_id: str) -> list[str]:
        rows = self.db.read(
            "SELECT job_id FROM session_jobs WHERE session_id=?"
            " ORDER BY created_at", (session_id,))
        return [r["job_id"] for r in rows]

    def snapshot(self, session_id: str, workspace_root: Path,
                 job_dir_of) -> Path | None:
        """登记本会话全部任务的材料/产物清单到会话目录（不复制全文）。"""
        job_ids = self.job_ids(session_id)
        if not job_ids:
            return None
        materials = []
        for job_id in job_ids:
            job_dir = job_dir_of(job_id)
            sources_path = job_dir / "sources.json"
            if sources_path.exists():
                try:
                    index = json.loads(sources_path.read_text(encoding="utf-8"))
                    materials.append({
                        "job_id": job_id,
                        "sources": [{"source_id": s.get("source_id"),
                                     "display": s.get("display"),
                                     "kind": s.get("kind"),
                                     "status": s.get("status")}
                                    for s in index.get("sources", [])]})
                except Exception:
                    materials.append({"job_id": job_id, "sources": [],
                                      "note": "sources.json 无法解析"})
        directory = workspace_root / "sessions" / session_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "materials.json"
        write_json(path, {"session_id": session_id,
                          "goal": self.goal(session_id),
                          "jobs": materials,
                          "note": "材料快照仅登记引用；全文在各自任务目录，"
                                  "不跨会话共享"})
        return path

    def load(self, session_id: str, workspace_root: Path) -> dict | None:
        path = workspace_root / "sessions" / session_id / "materials.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raise ValueError(f"会话材料快照损坏，拒绝伪装为空：{path}") from None
