# -*- coding: utf-8 -*-
"""D8-04：待输入状态持久化。"""
from __future__ import annotations

import hashlib
import json
import uuid

from src.harness.state.db import StateDb

PENDING = "pending"
ANSWERED = "answered"
INVALID = "invalid"
EXPIRED = "expired"


def _params_hash(params: dict) -> str:
    return hashlib.sha1(json.dumps(params, ensure_ascii=False,
                                   sort_keys=True).encode("utf-8")).hexdigest()


class PendingInputStore:
    def __init__(self, db: StateDb, *, clock=None):
        self.db = db
        self.clock = clock or db.now

    def create(self, *, job_id: str, questions: list[str], target: str,
               params: dict, budget: dict, plan_version: int = 1,
               ttl_seconds: float = 86400) -> dict:
        now = self.clock()
        input_id = "input_" + uuid.uuid4().hex[:12]
        expires_at = now + max(1.0, float(ttl_seconds))
        with self.db.write_tx() as conn:
            conn.execute(
                "INSERT INTO pending_inputs(input_id, job_id, questions_json, target,"
                " params_hash, budget_json, plan_version, status, answer_json,"
                " created_at, updated_at, expires_at)"
                " VALUES(?,?,?,?,?,?,?,?,'',?,?,?)",
                (input_id, job_id, json.dumps(questions, ensure_ascii=False), target,
                 _params_hash(params), json.dumps(budget, ensure_ascii=False),
                 int(plan_version), PENDING, now, now, expires_at))
        return self.get(input_id)

    def get(self, input_id: str, *, now: float | None = None) -> dict | None:
        rows = self.db.read(
            "SELECT * FROM pending_inputs WHERE input_id=?", (input_id,))
        if not rows:
            return None
        item = dict(rows[0])
        current = self.clock() if now is None else now
        if item["status"] == PENDING and item["expires_at"] < current:
            with self.db.write_tx() as conn:
                conn.execute(
                    "UPDATE pending_inputs SET status=?, updated_at=? WHERE input_id=? AND status=?",
                    (EXPIRED, current, input_id, PENDING))
            item["status"] = EXPIRED
        item["questions"] = json.loads(item.pop("questions_json") or "[]")
        item["budget"] = json.loads(item.pop("budget_json") or "{}")
        item["answer"] = json.loads(item.pop("answer_json") or "null")
        return item

    def list(self, job_id: str | None = None) -> list[dict]:
        sql = "SELECT input_id FROM pending_inputs"
        params = ()
        if job_id:
            sql += " WHERE job_id=?"
            params = (job_id,)
        sql += " ORDER BY created_at DESC"
        return [self.get(row["input_id"]) for row in self.db.read(sql, params)]

    def answer(self, input_id: str, answer: dict) -> dict:
        item = self.get(input_id)
        if item is None:
            raise LookupError(input_id)
        if item["status"] not in (PENDING,):
            raise ValueError(f"待输入已处于 {item['status']}，不能再次回答")
        with self.db.write_tx() as conn:
            conn.execute(
                "UPDATE pending_inputs SET status=?, answer_json=?, updated_at=?"
                " WHERE input_id=? AND status=?",
                (ANSWERED, json.dumps(answer, ensure_ascii=False),
                 self.clock(), input_id, PENDING))
        return self.get(input_id)

    def invalidate(self, job_id: str, *, reason: str = "") -> int:
        with self.db.write_tx() as conn:
            cursor = conn.execute(
                "UPDATE pending_inputs SET status=?, updated_at=?"
                " WHERE job_id=? AND status=?",
                (INVALID, self.clock(), job_id, PENDING))
            return cursor.rowcount