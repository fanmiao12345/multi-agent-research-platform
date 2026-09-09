# -*- coding: utf-8 -*-
"""
harness/state/ops.py —— 持久化操作账本（S4-08）

- op_key = job_id:action:action_version:params_hash —— 幂等键绑定业务动作与版本；
- 状态 pending → running → succeeded / failed；崩溃遗留的 running/pending
  启动时标记 unknown，绝不自动重放：只有 succeeded 已知成功才回放，
  unknown 必须先人工核实（返回 OperationUnknown 让调用方明确处理）；
- 不保存消息/密钥正文，只存结果文本与异常类型。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from src.harness.state.db import StateDb

PENDING = "pending"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
UNKNOWN = "unknown"

_STATES = (PENDING, RUNNING, SUCCEEDED, FAILED, UNKNOWN)


class OperationUnknown(RuntimeError):
    """崩溃遗留操作：结果未知，禁止自动重放，需人工核实。"""


def params_hash(params: dict) -> str:
    return hashlib.sha1(
        json.dumps(params, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


@dataclass
class OpResult:
    result: str
    mode: str          # executed | replayed | skipped_failed | unknown
    op_key: str = ""


class OperationLedger:
    def __init__(self, db: StateDb, job_id: str):
        self.db = db
        self.job_id = job_id

    def _key(self, action: str, action_version: int, params: dict) -> str:
        return f"{self.job_id}:{action}:{action_version}:{params_hash(params)}"

    def execute_once(self, action: str, params: dict, func, *,
                     action_version: int = 1) -> OpResult:
        """执行一次副作用操作；succeeded 的已知成功直接回放。

        func 抛错 → 记 failed（结果与异常类型），下次重放由调用方决定。
        """
        op_key = self._key(action, action_version, params)
        now = self.db.now()
        with self.db.write_tx() as conn:
            row = conn.execute(
                "SELECT status, result_json, error_type FROM operations WHERE op_key=?",
                (op_key,)).fetchone()
            if row is not None:
                if row["status"] == SUCCEEDED:
                    return OpResult(result=row["result_json"], mode="replayed",
                                    op_key=op_key)
                if row["status"] == FAILED:
                    return OpResult(result=row["error_type"], mode="skipped_failed",
                                    op_key=op_key)
                raise OperationUnknown(
                    f"操作 {action} 处于 {row['status']}：进程可能中断，"
                    "结果未知，禁止自动重放，请核实后处理")
            self.db.conn.execute(
                "INSERT OR IGNORE INTO operations(op_key, job_id, action,"
                " action_version, params_hash, status, result_json, error_type,"
                " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (op_key, self.job_id, action, action_version,
                 params_hash(params), PENDING, "", "", now, now))
            self.db.conn.execute(
                "UPDATE operations SET status=?, updated_at=? WHERE op_key=?",
                (RUNNING, now, op_key))
        try:
            result = str(func())
        except BaseException as e:  # noqa: BLE001 —— 只记录类型，不写异常正文
            with self.db.write_tx() as conn:
                conn.execute(
                    "UPDATE operations SET status=?, error_type=?, updated_at=? "
                    "WHERE op_key=?", (FAILED, type(e).__name__, now, op_key))
            return OpResult(result=type(e).__name__, mode="skipped_failed",
                            op_key=op_key)
        with self.db.write_tx() as conn:
            conn.execute(
                "UPDATE operations SET status=?, result_json=?, updated_at=? "
                "WHERE op_key=?", (SUCCEEDED, result, now, op_key))
        return OpResult(result=result, mode="executed", op_key=op_key)

    def mark_unknown(self) -> list[str]:
        """启动对账：running/pending → unknown（不自动重放）。返回受影响 op。"""
        rows = []
        with self.db.write_tx() as conn:
            for row in conn.execute(
                    "SELECT op_key FROM operations WHERE job_id=? AND status IN (?,?)",
                    (self.job_id, PENDING, RUNNING)):
                conn.execute(
                    "UPDATE operations SET status=?, updated_at=? WHERE op_key=?",
                    (UNKNOWN, self.db.now(), row["op_key"]))
                rows.append(row["op_key"])
        return rows

    def record(self, action: str, status: str, params: dict | None = None,
               result: str = "") -> str:
        """供非 func 形式显式记账（如外部动作完成回调）。"""
        assert status in _STATES
        op_key = self._key(action, 1, params or {})
        now = self.db.now()
        with self.db.write_tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO operations(op_key, job_id, action,"
                " action_version, params_hash, status, result_json, error_type,"
                " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (op_key, self.job_id, action, 1, params_hash(params or {}),
                 status, result, "", now, now))
        return op_key

    def list(self, *, limit: int = 200) -> list[dict]:
        rows = self.db.read(
            "SELECT op_key, action, action_version, status, result_json,"
            " error_type, created_at, updated_at FROM operations"
            " WHERE job_id=? ORDER BY rowid DESC LIMIT ?",
            (self.job_id, limit))
        return [dict(r) for r in rows]


def new_operation_id() -> str:
    return uuid.uuid4().hex
