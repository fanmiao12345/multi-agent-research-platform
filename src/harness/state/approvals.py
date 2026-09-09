# -*- coding: utf-8 -*-
"""
harness/state/approvals.py —— 持久化审批（S4-09）

- 审批绑定 job + 动作名 + 参数哈希 + 权限范围 + 动作版本 + 到期时间；
- 参数变化/重规划/取消：同一动作创建新审批时旧 pending 自动失效；
  显式 invalidate_for 支持计划版本变更/取消失效；
- 判定只认"未过期 + 哈希/范围/版本匹配 + granted"的授权，不凭审批者字符串放行。
"""
from __future__ import annotations

import time
import uuid

from src.harness.state.db import StateDb
from src.harness.state.ops import params_hash

PENDING = "pending"
GRANTED = "granted"
REJECTED = "rejected"
INVALID = "invalid"
EXPIRED = "expired"
DEFAULT_TTL_SECONDS = 300.0


class ApprovalError(ValueError):
    """审批不存在/已决策/已过期/已失效。"""


class ApprovalStore:
    def __init__(self, db: StateDb, job_id: str, *, clock=time.time):
        self.db = db
        self.job_id = job_id
        self.clock = clock

    def create(self, action_name: str, params: dict, *, scope: str = "default",
               version: int = 1, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> dict:
        """登记一条待审批；同动作仍在等待的旧审批自动失效（参数/版本变化语义）。"""
        now = self.clock()
        approval_id = uuid.uuid4().hex
        digest = params_hash(params)
        with self.db.write_tx() as conn:
            conn.execute(
                "UPDATE approvals SET status=?, decided_at=? WHERE job_id=? "
                "AND action_name=? AND status=? AND approval_id<>?",
                (INVALID, now, self.job_id, action_name, PENDING, approval_id))
            conn.execute(
                "INSERT INTO approvals(approval_id, job_id, action_name,"
                " params_hash, scope, version, status, created_at, expires_at,"
                " decided_at) VALUES(?,?,?,?,?,?,?,?,?,0)",
                (approval_id, self.job_id, action_name, digest, scope, version,
                 PENDING, now, now + ttl_seconds))
        return self._row(approval_id)

    def decide(self, approval_id: str, action: str) -> dict:
        """action='approve'|'reject'；过期或已失效拒绝决策（返回原因而非静默放行）。"""
        if action == "approve":
            target = GRANTED
        elif action == "reject":
            target = REJECTED
        else:
            raise ApprovalError("action 必须为 approve/reject")
        row = self._row(approval_id)
        if row is None:
            raise ApprovalError("审批不存在")
        if row["status"] != PENDING:
            raise ApprovalError(f"审批已决策或已失效：{row['status']}")
        if self.clock() > row["expires_at"]:
            with self.db.write_tx() as conn:
                conn.execute(
                    "UPDATE approvals SET status=?, decided_at=? WHERE approval_id=?",
                    (EXPIRED, self.clock(), approval_id))
            raise ApprovalError("审批已过期，请重新发起")
        with self.db.write_tx() as conn:
            conn.execute(
                "UPDATE approvals SET status=?, decided_at=? WHERE approval_id=?",
                (target, self.clock(), approval_id))
        return self._row(approval_id)

    def valid_for(self, action_name: str, params: dict, *, scope: str = "default",
                  version: int = 1) -> dict | None:
        """当前是否存在对该动作+参数+范围+版本的有效授权（granted 且未过期）。"""
        now = self.clock()
        rows = self.db.read(
            "SELECT * FROM approvals WHERE job_id=? AND action_name=? "
            "AND params_hash=? AND scope=? AND version=? AND status=? "
            "AND expires_at >= ? ORDER BY created_at DESC LIMIT 1",
            (self.job_id, action_name, params_hash(params), scope, version,
             GRANTED, now))
        return dict(rows[0]) if rows else None

    def invalidate_for(self, action_name: str) -> int:
        """显式失效（重规划/取消/来源改变）：返回失效条数。"""
        now = self.clock()
        with self.db.write_tx() as conn:
            cursor = conn.execute(
                "UPDATE approvals SET status=?, decided_at=? WHERE job_id=? "
                "AND action_name=? AND status=?",
                (INVALID, now, self.job_id, action_name, PENDING))
            return cursor.rowcount

    def list(self) -> list[dict]:
        rows = self.db.read(
            "SELECT approval_id, action_name, scope, version, status,"
            " created_at, expires_at, decided_at FROM approvals"
            " WHERE job_id=? ORDER BY created_at DESC", (self.job_id,))
        return [dict(r) for r in rows]

    def _row(self, approval_id: str) -> dict | None:
        rows = self.db.read(
            "SELECT * FROM approvals WHERE approval_id=?", (approval_id,))
        return dict(rows[0]) if rows else None
