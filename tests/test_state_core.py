# -*- coding: utf-8 -*-
"""测试：状态库/队列租约/取消/操作账本/审批/会话（S4-01/02/03/06/08/09/13）。"""
import json
import sqlite3
import threading
import time

import pytest

from src.harness.state import states
from src.harness.state.approvals import ApprovalError, ApprovalStore
from src.harness.state.db import StateDb, StateDbError
from src.harness.state.ops import OperationLedger, OperationUnknown
from src.harness.state.queue import JobQueue
from src.harness.state.sessions import SessionStore


@pytest.fixture()
def db(tmp_path):
    handle = StateDb(tmp_path / "state.sqlite")
    yield handle
    handle.close()


# ---- S4-01/13：库、损坏与备份 -------------------------------------------
def test_db_creates_schema_and_version(db):
    version = db.conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert version[0] == "3"
    tables = {r[0] for r in db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "sessions", "session_jobs", "approvals", "operations"} <= tables


def test_db_corrupt_file_is_explicit_error(tmp_path):
    path = tmp_path / "broken.sqlite"
    path.write_bytes(b"this is not a sqlite database at all" * 10)
    with pytest.raises(StateDbError, match="损坏|不是 SQLite"):
        StateDb(path)


def test_db_version_ahead_is_refused(tmp_path):
    path = tmp_path / "future.sqlite"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES('schema_version','99')")
    conn.commit()
    conn.close()
    with pytest.raises(StateDbError, match="版本"):
        StateDb(path)


def test_db_backup_and_verify(db, tmp_path):
    queue = JobQueue(db)
    queue.submit(request={"task": "t"})
    backup = db.backup_to(tmp_path / "backup" / "state.sqlite")
    assert backup.exists()
    from src.harness.state.db import verify_backup
    assert verify_backup(backup)
    probe = StateDb(backup)
    assert JobQueue(probe).get  # 可打开
    probe.close()


# ---- S4-02：状态词与迁移 ------------------------------------------------
def test_state_machine_vocabulary_and_transitions():
    with pytest.raises(states.StateError):
        states.validate("bogus")
    assert states.can_transition("queued", "cancelled")
    assert not states.can_transition("queued", "completed")
    assert not states.can_transition("completed", "running")
    assert states.map_run_status("completed") == states.COMPLETED
    assert states.map_run_status("CANCELLED") == states.CANCELLED
    with pytest.raises(states.StateError):
        states.map_run_status("mystery")
    for status in states.ALL:
        if status in states.TERMINAL:
            assert states.is_terminal(status)
        else:
            assert not states.is_terminal(status)


# ---- S4-03/06：队列租约与取消 -------------------------------------------
def test_queue_claim_unique_and_release(db):
    queue = JobQueue(db)
    job_id = queue.submit(request={"task": "t1"})
    second = queue.submit(request={"task": "t2"})
    claims = queue.claim("worker-a", lease_seconds=30, limit=5)
    assert {c.job_id for c in claims} == {job_id, second}
    assert len(queue.claim("worker-b", lease_seconds=30)) == 0  # 同一时刻仅一个执行者
    queue.release("worker-a", job_id, to=states.COMPLETED, stage="done")
    row = queue.get(job_id)
    assert row["status"] == states.COMPLETED and row["lease_owner"] == ""
    # 终态不可再 release
    with pytest.raises(states.StateError):
        queue.release("worker-a", job_id, to=states.PARTIAL)


def test_queue_lease_expiry_and_startup_scan(db, monkeypatch):
    queue = JobQueue(db)
    job_id = queue.submit(request={"task": "t"})
    queue.claim("worker-a", lease_seconds=30)
    # 模拟时间流逝：租约过期
    monkeypatch.setattr(db, "now", lambda: time.time() + 60)
    found = queue.startup_scan(lease_seconds=30)
    assert found == [{"job_id": job_id}]
    row = queue.get(job_id)
    assert row["status"] == states.INTERRUPTED
    # 过期后可被重新领取（禁止双执行者的窗口已关闭）
    monkeypatch.setattr(db, "now", lambda: time.time())
    claims = queue.claim("worker-b", lease_seconds=30)
    assert [c.job_id for c in claims] == [job_id]
    # 未过期的租约不能被扫掉
    monkeypatch.setattr(db, "now", lambda: time.time() + 10)
    assert queue.startup_scan(lease_seconds=30) == []


def test_queue_concurrent_claims_single_winner(db):
    queue = JobQueue(db)
    for index in range(6):
        queue.submit(request={"task": f"t{index}"})
    winners = []
    lock = threading.Lock()

    def worker(name):
        claim = queue.claim(name, lease_seconds=60)
        if claim:
            with lock:
                winners.append(claim[0].job_id)
            time.sleep(0.05)  # 模拟执行
            queue.release(name, claim[0].job_id, to=states.COMPLETED)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 每份 job 恰好被一个执行者做完：没有重复执行
    assert len(winners) == 6 and len(set(winners)) == 6
    running = [r for r in queue.list() if r["status"] == states.RUNNING]
    assert running == []


def test_queue_cancel_two_phase(db):
    queue = JobQueue(db)
    queued_id = queue.submit(request={"task": "等待"})
    assert queue.request_cancel(queued_id) == {"status": "stopped",
                                               "state": states.CANCELLED}
    running_id = queue.submit(request={"task": "运行中"})
    assert queue.claim("worker-a", lease_seconds=60)[0].job_id == running_id
    outcome = queue.request_cancel(running_id)
    assert outcome["status"] == "requested"
    assert queue.get(running_id)["status"] == states.CANCEL_REQUESTED
    # 执行边界收敛：partial 保留产物（不冒充 completed）
    queue.resume_after_stop(running_id, outcome=states.PARTIAL, message="已有部分产物")
    row = queue.get(running_id)
    assert row["status"] == states.PARTIAL and row["message"] == "已有部分产物"
    with pytest.raises(states.StateError):
        queue.resume_after_stop(running_id, outcome=states.COMPLETED)


# ---- S4-08：操作账本 -----------------------------------------------------
def test_op_ledger_execute_replay_and_unknown(db):
    ledger = OperationLedger(db, "job_x")
    calls = []

    def side_effect():
        calls.append(1)
        return "ok-1"
    first = ledger.execute_once("write_report", {"text": "A"}, side_effect)
    assert first.mode == "executed" and first.result == "ok-1"
    replay = ledger.execute_once("write_report", {"text": "A"}, side_effect)
    assert replay.mode == "replayed" and calls == [1]          # 已知成功才回放
    failed = ledger.execute_once("fragile", {}, lambda: 1 / 0)
    assert failed.mode == "skipped_failed"
    fragile_row = next(r for r in ledger.list() if r["action"] == "fragile")
    assert fragile_row["error_type"] == "ZeroDivisionError"
    # 参数不同 = 不同幂等键
    other = ledger.execute_once("write_report", {"text": "B"}, side_effect)
    assert other.mode == "executed" and len(calls) == 2


def test_op_ledger_crash_window_marks_unknown_no_autoreplay(db):
    ledger = OperationLedger(db, "job_x")
    key = ledger.record("external_send", "running", params={"to": "x"})
    marked = ledger.mark_unknown()
    assert marked == [key]
    assert ledger.list()[0]["status"] == "unknown"
    with pytest.raises(OperationUnknown):
        ledger.execute_once("external_send", {"to": "x"}, lambda: "again")
    # unknown 的副作用不会在未知状态下自动执行第二次
    assert [r for r in ledger.list() if r["status"] == "unknown"]


# ---- S4-09：审批持久化与失效 --------------------------------------------
def test_approvals_bound_to_params_scope_version_and_expiry(db):
    store = ApprovalStore(db, "job_x")
    approval = store.create("publish", {"path": "report.md"}, version=1)
    assert store.valid_for("publish", {"path": "report.md"}) is None   # 尚未批准
    store.decide(approval["approval_id"], "approve")
    granted = store.valid_for("publish", {"path": "report.md"}, version=1)
    assert granted is not None
    # 参数变化 → 旧授权对该参数不可用（哈希绑定，不凭动作名放行）
    assert store.valid_for("publish", {"path": "other.md"}, version=1) is None
    # 同一动作再来一条 pending（如重试提交）→ 显式失效只影响 pending
    second = store.create("publish", {"path": "report.md"}, version=1)
    assert second["status"] == "pending"
    assert store.invalidate_for("publish") >= 1
    with pytest.raises(ApprovalError, match="已决策|已失效"):
        store.decide(second["approval_id"], "approve")


def test_approval_expiry_and_scope(db):
    now = [time.time()]
    store = ApprovalStore(db, "job_x", clock=lambda: now[0])
    approval = store.create("run", {"cmd": "x"}, scope="web", ttl_seconds=10)
    now[0] += 30
    with pytest.raises(ApprovalError, match="过期"):
        store.decide(approval["approval_id"], "approve")
    # 过期后不再算有效授权
    assert store.valid_for("run", {"cmd": "x"}, scope="web") is None
    # 范围不同不放行
    store2 = ApprovalStore(db, "job_x", clock=lambda: now[0])
    fresh = store2.create("run", {"cmd": "x"}, scope="web", ttl_seconds=10)
    now[0] += 1
    store2.decide(fresh["approval_id"], "approve")
    assert store2.valid_for("run", {"cmd": "x"}, scope="cli") is None


# ---- S4-05：会话材料归属 ------------------------------------------------
def test_sessions_keep_materials_isolated(db, tmp_path):
    sessions = SessionStore(db)
    queue = JobQueue(db)
    sid_a = sessions.create("会话甲")
    sid_b = sessions.create("会话乙")
    job_a = queue.submit(request={"task": "甲"})
    job_b = queue.submit(request={"task": "乙"})
    sessions.attach_job(sid_a, job_a)
    sessions.attach_job(sid_b, job_b)
    job_dir_a = tmp_path / "jobs" / job_a
    job_dir_a.mkdir(parents=True)
    (job_dir_a / "sources.json").write_text(json.dumps(
        {"sources": [{"source_id": "src_a", "display": "甲材料.md",
                      "kind": "file", "status": "ok"}]}, ensure_ascii=False),
        encoding="utf-8")
    sessions.snapshot(sid_a, tmp_path, lambda jid: tmp_path / "jobs" / jid)
    snapshot_a = sessions.load(sid_a, tmp_path)
    snapshot_b = sessions.load(sid_b, tmp_path)
    assert snapshot_a["goal"] == "会话甲"
    assert snapshot_a["jobs"][0]["job_id"] == job_a
    assert "甲材料.md" in json.dumps(snapshot_a, ensure_ascii=False)
    # 会话乙没有任何甲的资料（不跨会话共享）
    assert snapshot_b is None or "甲材料" not in json.dumps(snapshot_b, ensure_ascii=False)
