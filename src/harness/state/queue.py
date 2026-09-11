# -*- coding: utf-8 -*-
"""
harness/state/queue.py —— 有界任务队列与持久化租约（S4-03/06）

- 提交即落库（queued）；领取 = 单条原子 UPDATE（WHERE 子查询 LIMIT 1），
  只有 queued / interrupted，或"租约已过期的 running / waiting_human /
  cancel_requested"能被领取 → 禁止两个执行者同时恢复同一任务（进程内由
  写锁串行，跨进程由 UPDATE 原子性兜底）；
- 领取后租约需心跳续期；进程崩溃后租约过期 → startup_scan 标记 interrupted；
- 取消分两段：queued 直接 cancelled（stopped）；运行中只置 cancel_requested
  （requested），由执行边界检查后收敛为 cancelled/partial（S4-06）。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from src.harness.state import states
from src.harness.state.db import StateDb

_ELIGIBLE = ("status IN (?, ?) OR "
             "(status IN (?, ?, ?) AND lease_expires < ?)")


@dataclass
class QueueClaim:
    job_id: str
    session_id: str
    kind: str
    stage: str
    request_json: str
    status: str
    cancel_requested: bool
    lease_owner: str
    message: str = ""
    parent_job_id: str = ""
    plan_version: int = 1

    def as_dict(self) -> dict:
        return {"job_id": self.job_id, "session_id": self.session_id,
                "kind": self.kind, "stage": self.stage, "status": self.status,
                "cancel_requested": self.cancel_requested,
                "lease_owner": self.lease_owner, "message": self.message,
                "parent_job_id": self.parent_job_id,
                "plan_version": self.plan_version}


class JobQueue:
    def __init__(self, db: StateDb):
        self.db = db

    # ---- 提交 ------------------------------------------------------------
    def submit(self, *, job_id: str | None = None, session_id: str = "",
               kind: str = "agent", request: dict, stage: str = "",
               parent_job_id: str = "", plan_version: int = 1) -> str:
        job_id = job_id or ("job_" + uuid.uuid4().hex)
        import json
        now = self.db.now()
        try:
            with self.db.write_tx() as conn:
                conn.execute(
                    "INSERT INTO jobs(job_id, session_id, kind, status, stage,"
                    " request_json, parent_job_id, plan_version, stage_history_json,"
                    " cancel_requested, lease_owner, lease_expires,"
                    " error, message, created_at, updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,'[]',0,'',0,'','',?,?)",
                    (job_id, session_id, kind, states.QUEUED, stage,
                     json.dumps(request, ensure_ascii=False), parent_job_id,
                     int(plan_version), now, now))
        except Exception as e:
            from src.harness.state.db import StateDbError
            if "UNIQUE" in str(e).upper():
                raise StateDbError(f"任务已存在，禁止重复提交：{job_id}") from e
            raise
        return job_id

    # ---- 领取/租约 -------------------------------------------------------
    def claim(self, owner: str, *, lease_seconds: float = 120.0,
              limit: int = 1) -> list[QueueClaim]:
        now = self.db.now()
        rows = []
        for _ in range(limit):
            with self.db.write_tx() as conn:
                candidate = conn.execute(
                    "SELECT job_id, session_id, kind, stage, request_json,"
                    " status, cancel_requested, parent_job_id, plan_version"
                    " FROM jobs WHERE "
                    + _ELIGIBLE + " ORDER BY created_at, rowid LIMIT 1",
                    (states.QUEUED, states.INTERRUPTED,
                     states.RUNNING, states.WAITING_HUMAN,
                     states.CANCEL_REQUESTED, now)).fetchone()
                if candidate is None:
                    break
                updated = conn.execute(
                    "UPDATE jobs SET status=?, lease_owner=?, lease_expires=?,"
                    " updated_at=? WHERE job_id=? AND status IN (?,?,?,?,?) "
                    "AND lease_expires < ?",
                    (states.RUNNING, owner, now + lease_seconds, now,
                     candidate["job_id"], states.QUEUED, states.INTERRUPTED,
                     states.RUNNING, states.WAITING_HUMAN,
                     states.CANCEL_REQUESTED, now))
                if updated.rowcount != 1:
                    continue  # 被其他执行者抢先：跳过该候选
            rows.append(QueueClaim(
                job_id=candidate["job_id"], session_id=candidate["session_id"],
                kind=candidate["kind"], stage=candidate["stage"],
                request_json=candidate["request_json"],
                status=states.RUNNING,
                cancel_requested=bool(candidate["cancel_requested"]),
                lease_owner=owner,
                parent_job_id=candidate["parent_job_id"] or "",
                plan_version=int(candidate["plan_version"] or 1)))
        return rows

    def heartbeat(self, owner: str, job_id: str, *, lease_seconds: float) -> bool:
        now = self.db.now()
        with self.db.write_tx() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET lease_expires=?, updated_at=? WHERE job_id=? "
                "AND lease_owner=? AND status IN (?, ?, ?)",
                (now + lease_seconds, now, job_id, owner,
                 states.RUNNING, states.WAITING_HUMAN, states.CANCEL_REQUESTED))
            return cursor.rowcount == 1

    def release(self, owner: str, job_id: str, *, to: str,
                stage: str = "", error: str = "", message: str = "") -> None:
        """执行者主动交回终态（partial 也是终态）；迁移必须合法。"""
        states.validate(to)
        now = self.db.now()
        with self.db.write_tx() as conn:
            current = conn.execute(
                "SELECT status FROM jobs WHERE job_id=? AND lease_owner=?",
                (job_id, owner)).fetchone()
            if current is None:
                raise states.StateError(
                    f"无法结束任务 {job_id}：租约不属于 {owner} 或任务不存在")
            if states.is_terminal(current["status"]):
                raise states.StateError(f"任务 {job_id} 已是终态 {current['status']}")
            states.transition(current["status"], to)
            conn.execute(
                "UPDATE jobs SET status=?, stage=?, lease_owner='',"
                " lease_expires=0, error=?, message=?, updated_at=?"
                " WHERE job_id=? AND lease_owner=?",
                (to, stage, error, message, now, job_id, owner))

    def update_progress(self, job_id: str, *, stage: str = "",
                       plan_version: int | None = None, message: str = "") -> None:
        """持久化阶段/计划版本，并追加可审计的阶段历史。"""
        import json
        now = self.db.now()
        with self.db.write_tx() as conn:
            row = conn.execute(
                "SELECT stage_history_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise LookupError(job_id)
            try:
                history = json.loads(row["stage_history_json"] or "[]")
            except ValueError:
                history = []
            if stage:
                history.append({"stage": stage, "at": now})
            if plan_version is None:
                conn.execute(
                    "UPDATE jobs SET stage=?, stage_history_json=?, message=?, updated_at=? WHERE job_id=?",
                    (stage or "", json.dumps(history, ensure_ascii=False), message, now, job_id))
            else:
                conn.execute(
                    "UPDATE jobs SET stage=?, plan_version=?, stage_history_json=?, message=?, updated_at=? WHERE job_id=?",
                    (stage or "", int(plan_version), json.dumps(history, ensure_ascii=False), message, now, job_id))

    def merge_request(self, job_id: str, patch: dict) -> None:
        import json
        with self.db.write_tx() as conn:
            row = conn.execute(
                "SELECT request_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise LookupError(job_id)
            try:
                payload = json.loads(row["request_json"] or "{}")
            except ValueError:
                payload = {}
            payload.update(patch or {})
            conn.execute(
                "UPDATE jobs SET request_json=?, updated_at=? WHERE job_id=?",
                (json.dumps(payload, ensure_ascii=False), self.db.now(), job_id))

    # ---- 取消（S4-06 分两段） ---------------------------------------------
    def request_cancel(self, job_id: str) -> dict:
        """queued → 直接 cancelled（stopped）；运行中 → 只记 cancel_requested。"""
        with self.db.write_tx() as conn:
            row = conn.execute(
                "SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise LookupError(f"任务不存在：{job_id}")
        if row["status"] == states.QUEUED:
            with self.db.write_tx() as conn:
                conn.execute(
                    "UPDATE jobs SET status=?, updated_at=? WHERE job_id=?",
                    (states.CANCELLED, self.db.now(), job_id))
            return {"status": "stopped", "state": states.CANCELLED}
        if states.is_terminal(row["status"]):
            return {"status": "already_final", "state": row["status"]}
        with self.db.write_tx() as conn:
            conn.execute(
                "UPDATE jobs SET cancel_requested=1, status=?, updated_at=? "
                "WHERE job_id=? AND status NOT IN (?,?,?,?,?)",
                (states.CANCEL_REQUESTED, self.db.now(), job_id,
                 states.COMPLETED, states.FAILED, states.CANCELLED,
                 states.PARTIAL, states.INTERRUPTED))
        return {"status": "requested", "state": states.CANCEL_REQUESTED}

    def resume_after_stop(self, job_id: str, *, outcome: str,
                          stage: str = "", message: str = "") -> None:
        """执行边界看到 cancel_requested 后收敛为终态（S4-06 的"已停止"）。"""
        if outcome not in (states.CANCELLED, states.PARTIAL, states.COMPLETED):
            raise states.StateError(
                f"停止后的收敛结果只能是 cancelled/partial/completed：{outcome}")
        with self.db.write_tx() as conn:
            row = conn.execute(
                "SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise LookupError(job_id)
            if states.is_terminal(row["status"]):
                raise states.StateError(
                    f"任务 {job_id} 已是终态 {row['status']}，不能重复收敛")
            conn.execute(
                "UPDATE jobs SET status=?, stage=?, lease_owner='',"
                " lease_expires=0, message=?, updated_at=? WHERE job_id=?",
                (outcome, stage, message, self.db.now(), job_id))

    # ---- 启动扫描（S4-03） ------------------------------------------------
    def startup_scan(self, *, lease_seconds: float = 120.0) -> list[dict]:
        """把租约过期的运行中任务标记 interrupted；有效租约的任务不碰。"""
        now = self.db.now()
        rows = []
        with self.db.write_tx() as conn:
            for row in conn.execute(
                    "SELECT job_id FROM jobs WHERE status IN (?,?,?) "
                    "AND lease_expires < ? ORDER BY created_at",
                    (states.RUNNING, states.WAITING_HUMAN,
                     states.CANCEL_REQUESTED, now)):
                conn.execute(
                    "UPDATE jobs SET status=?, lease_owner='', lease_expires=0,"
                    " updated_at=? WHERE job_id=? AND lease_expires < ?",
                    (states.INTERRUPTED, now, row["job_id"], now))
                rows.append({"job_id": row["job_id"]})
        return rows

    # ---- 查看 ------------------------------------------------------------
    def get(self, job_id: str) -> dict | None:
        rows = self.db.read(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,))
        return dict(rows[0]) if rows else None

    def list(self, *, limit: int = 100) -> list[dict]:
        rows = self.db.read(
            "SELECT job_id, session_id, kind, status, stage, cancel_requested,"
            " error, message, created_at, updated_at FROM jobs"
            " ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


def _clock() -> float:
    return time.time()
