# -*- coding: utf-8 -*-
"""
harness/state/db.py —— SQLite 状态库（S4-01/13）

- 标准库 sqlite3，单文件 workspaces/state.sqlite（实用化计划 4.3 布局）；
- meta 表记录 schema_version（应用版本，不依赖 PRAGMA 的 DDL 计数）；
  当前版本 v1；损坏/无法识别时抛 StateDbError（可操作提示），绝不以空库掩盖丢失；
- 连接 check_same_thread=False + 全局写锁：线程池/子执行者可安全共享同一实例；
  每次写操作走 write_tx()（锁 + 事务）。

诚实边界：SQLite 负责状态/账本一致性；大文本仍存文件（先写文件再登记），
文件系统与库之间由对账函数显式处理崩溃窗口（S4-04）。
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1


class StateDbError(RuntimeError):
    """状态库不可用/损坏/版本不识别：必须显式处理，不能静默重建。"""


def open_state_db(path: str | Path) -> "StateDb":
    return StateDb(Path(path))


class StateDb:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            self.conn = sqlite3.connect(str(self.path), timeout=15,
                                        check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA busy_timeout=10000")
        except sqlite3.DatabaseError as e:
            raise StateDbError(
                f"状态库损坏或不是 SQLite 文件：{self.path}（{e}）。"
                "不要删除或重建来掩盖历史：请从备份恢复，或人工核对任务产物") from e
        self._check_integrity()

    def _check_integrity(self) -> None:
        try:
            tables = {r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as e:
            raise StateDbError(
                f"状态库损坏或不是 SQLite 文件：{self.path}（{e}）。"
                "不要删除或重建来掩盖历史：请从备份恢复，或人工核对任务产物") from e
        if not tables:
            self._create_schema()
            return
        try:
            row = self.conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.DatabaseError as e:
            raise StateDbError(
                f"状态库 meta 不可读：{self.path}（{e}），禁止猜测后写入") from e
        version = int(row[0]) if row else 0
        if version > SCHEMA_VERSION:
            raise StateDbError(
                f"状态库版本 {version} 高于本程序支持的 {SCHEMA_VERSION}："
                "请升级程序或使用兼容版本打开，禁止降级写入")

    def _create_schema(self) -> None:
        with self._lock, self.conn:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'agent',   -- agent | research
                status TEXT NOT NULL,                 -- S4-02 状态词
                stage TEXT NOT NULL DEFAULT '',
                request_json TEXT NOT NULL DEFAULT '{}',
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                lease_owner TEXT NOT NULL DEFAULT '',
                lease_expires REAL NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                goal TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS session_jobs (
                session_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (session_id, job_id));
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                action_name TEXT NOT NULL,
                params_hash TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,       -- pending/granted/rejected/invalid/expired
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                decided_at REAL NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS idx_approvals_job
                ON approvals(job_id, status);
            CREATE TABLE IF NOT EXISTS operations (
                op_key TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                action TEXT NOT NULL,
                action_version INTEGER NOT NULL DEFAULT 1,
                params_hash TEXT NOT NULL,
                status TEXT NOT NULL,       -- pending/running/succeeded/failed/unknown
                result_json TEXT NOT NULL DEFAULT '',
                error_type TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_ops_job ON operations(job_id, status);
            """)
            self.conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),))

    # ---- 通用操作 ---------------------------------------------------------
    @contextmanager
    def write_tx(self):
        """写事务：全局锁串行化（跨线程）+ 自动提交/回滚。"""
        with self._lock:
            with self.conn:
                yield self.conn

    def read(self, sql: str, params=()):
        """读查询：不需要持有写锁。"""
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params=()):
        """单条写语句（调用方负责在 write_tx 内使用 conn 时勿用此）。"""
        with self._lock:
            with self.conn:
                return self.conn.execute(sql, params)

    def now(self) -> float:
        import time
        return time.time()

    def backup_to(self, dest: str | Path) -> Path:
        """在线备份到新文件（SQLite backup API）。"""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            backup = sqlite3.connect(str(dest))
            try:
                self.conn.backup(backup)
            finally:
                backup.close()
        return dest

    def close(self) -> None:
        try:
            with self._lock:
                self.conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def verify_backup(path: str | Path) -> bool:
    """备份可用性校验：能被只读打开并读出 schema_version 表。"""
    try:
        probe = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            row = probe.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
            return row is not None
        finally:
            probe.close()
    except sqlite3.DatabaseError:
        return False
