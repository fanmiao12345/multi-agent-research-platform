# -*- coding: utf-8 -*-
"""测试：S6 运维与业务评测（真实未配置不执行/机器检查/评分表/健康/备份/验证）。"""
import json
import os
import sys
from pathlib import Path

import pytest

from tests._s4_pipeline_brain import S4Brain

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---- S6-03：真实模式缺 Key → 整批 not_executed，零模型请求 -----------------
def test_real_without_key_is_not_executed_and_not_mocked(monkeypatch, tmp_path):
    from eval.business_eval import run_business_eval
    from config.settings import Settings
    monkeypatch.setenv("MODEL_API_KEY", "")
    settings = Settings()
    report = run_business_eval(workspace_root=tmp_path, mode="real", max_cost=0.02,
                               repeats=1, fault_rounds=1, settings=settings)
    assert report["meta"]["real_config_ready"] is False
    rows = [r for r in report["results"]
            if r["status"] == "not_executed"]
    assert rows and all("MODEL_API_KEY" in r["reason"] for r in rows)
    # 零模型请求：工作区没有任何 job 目录/账本
    assert not list(tmp_path.glob("jobs/job_*/ledger.json"))


# ---- S6-01/02/04/06：stub 成功路径、机器检查、快照 ------------------------
def test_business_eval_stub_success_and_machine_checks(tmp_path):
    from eval.business_eval import run_business_eval
    out = tmp_path / "out"
    report = run_business_eval(workspace_root=tmp_path / "ws", mode="mock",
                               llm=S4Brain(), repeats=2, fault_rounds=1,
                               task_filter="o01", out_dir=out)
    assert report["totals"]["attempts_total"] == 2
    assert report["totals"]["passed"] == 2
    record = report["records"][0]
    checks = record["machine_checks"]
    assert checks["citation_tokens"] >= 1
    assert checks["unresolved_citations"] == 0
    assert record["elapsed_seconds"] >= 0
    assert record["estimated_cost_usd"] == 0
    meta = report["meta"]
    assert meta["mode"] == "mock" and meta["real_config_ready"] is True
    assert meta["versions"]["python"].startswith("3.1")
    assert "MODEL_API_KEY" not in json.dumps(meta["config"])
    assert (out / "samples").exists()  # samples 目录由 out_dir 创建


def test_business_eval_batch_cap_stops_remaining(tmp_path):
    """O-09：--batch-max-cost 是整批累计上限（--max-cost 只是单任务上限）。"""
    from eval.business_eval import run_business_eval
    report = run_business_eval(workspace_root=tmp_path / "ws", mode="mock",
                               llm=S4Brain(), repeats=3, fault_rounds=1,
                               task_filter="o01", out_dir=tmp_path / "out",
                               batch_max_cost=0.0)
    assert report["totals"]["attempts_total"] == 0
    assert report["totals"]["skipped_or_not_executed"] == 3
    assert any("batch_max_cost" in (r.get("reason") or "") for r in report["results"])
    assert report["meta"]["batch_max_cost_usd"] == 0.0
    assert report["meta"]["batch_spent_usd"] == 0.0
    # 未设上限时行为不变（向后兼容）
    ok = run_business_eval(workspace_root=tmp_path / "ws2", mode="mock",
                           llm=S4Brain(), repeats=1, fault_rounds=1,
                           task_filter="o01", out_dir=tmp_path / "out2")
    assert ok["totals"]["passed"] == 1
    assert ok["meta"]["batch_max_cost_usd"] is None
    with pytest.raises(ValueError):
        run_business_eval(workspace_root=tmp_path / "ws3", mode="mock",
                          llm=S4Brain(), repeats=1, fault_rounds=1,
                          task_filter="o01", out_dir=tmp_path / "out3",
                          batch_max_cost=-1)


def test_business_eval_closed_book_by_default_and_open_book_switch(tmp_path):
    """S8-B：默认闭卷——关键事实/禁止断言留在评测端；open_book=True 仅作对照并标记。"""
    from eval.business_eval import run_business_eval
    out = tmp_path / "out"
    report = run_business_eval(workspace_root=tmp_path / "ws", mode="mock",
                               llm=S4Brain(), repeats=1, fault_rounds=1,
                               task_filter="o01", out_dir=out)
    assert report["meta"]["open_book"] is False           # 默认闭卷，报告自描述
    record = report["records"][0]
    assert record["status"] == "passed"
    hard = record["chain_hard_checks"]
    # 必需章节仍是用户可见任务要求，正常传入（o01：2 个必需章节）
    assert hard["required_sections_total"] == 2 and hard["required_section_hits"] == 2
    # 关键事实/禁止断言不再注入写作端（开卷关闭）
    assert hard["forbidden_total"] == 0 and hard["fact_total"] == 0
    job_dir = tmp_path / "ws" / "jobs" / record["root_job_id"]
    saved = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
    assert saved["required_sections"] == ["资料目录", "覆盖范围"]
    assert saved["forbidden_claims"] == [] and saved["key_facts"] == []

    # 对照开关：open_book=True 恢复旧行为，且 meta 显式标记不可与闭卷合并
    out2 = tmp_path / "out2"
    report2 = run_business_eval(workspace_root=tmp_path / "ws2", mode="mock",
                                llm=S4Brain(), repeats=1, fault_rounds=1,
                                task_filter="o01", out_dir=out2, open_book=True)
    assert report2["meta"]["open_book"] is True
    assert "开卷" in report2["meta"]["open_book_note"]
    record2 = report2["records"][0]
    hard2 = record2["chain_hard_checks"]
    assert hard2["forbidden_total"] == 1 and hard2["fact_total"] == 2
    saved2 = json.loads(((tmp_path / "ws2" / "jobs" / record2["root_job_id"])
                         / "request.json").read_text(encoding="utf-8"))
    assert saved2["forbidden_claims"] and saved2["key_facts"]


def test_business_eval_mock_failure_is_recorded_not_passed(tmp_path):
    from eval.business_eval import run_business_eval
    from src.llm.mock import MockLLM
    report = run_business_eval(workspace_root=tmp_path / "ws", mode="mock",
                               llm=MockLLM(), repeats=1, fault_rounds=1,
                               task_filter="o01", out_dir=tmp_path / "out")
    assert report["totals"]["failed"] == 1 and report["totals"]["passed"] == 0
    assert report["records"][0]["status"] == "failed"
    # 失败样本目录被保存
    samples = list((tmp_path / "out" / "samples").glob("o01_rep1"))
    assert samples and (samples[0] / "job.json").exists()


def test_business_eval_revision_executes_as_change(tmp_path):
    from eval.business_eval import run_business_eval
    from tests._s4_pipeline_brain import S4Brain
    report = run_business_eval(workspace_root=tmp_path / "ws", mode="mock",
                               llm=S4Brain(), repeats=1, fault_rounds=1,
                               task_filter="v01", out_dir=tmp_path / "out")
    # 改稿案例经 base_draft 链执行（不再跳过）
    assert report["totals"]["attempts_total"] == 1
    assert report["totals"]["passed"] == 1
    record = report["records"][0]
    assert record["revision_of"] == "fixture-draft-v1"
    assert record["changed"] is True
    assert report["totals"]["revision_skipped"] == 0


def test_fault_invalid_config_probe(tmp_path):
    from eval.business_eval import _fault_invalid_config
    import os
    os.environ["MODEL_API_KEY"] = ""
    ok, detail = _fault_invalid_config()
    assert ok and "MODEL_API_KEY" in detail


# ---- S6-05：人工评分表 ----------------------------------------------------
def test_human_score_sheets_generated(tmp_path):
    from eval.business_eval import run_business_eval
    from eval.human_scores import build_sheets
    out = tmp_path / "out"
    report = run_business_eval(workspace_root=tmp_path / "ws", mode="mock",
                               llm=S4Brain(), repeats=1, fault_rounds=1,
                               task_filter="o01", out_dir=out)
    sheets = build_sheets(report, tmp_path / "sheets")
    assert len(sheets) == 1
    text = sheets[0].read_text(encoding="utf-8-sig")
    assert "correctness" in text and "人工改稿分钟" in text
    assert "机器检查" in text and "不得作为自动盖章依据" in text


# ---- S6-08：健康检查/备份/全新环境验证 ------------------------------------
def test_health_checks_run(tmp_path):
    from src.ops.health import run_checks
    from config.settings import Settings
    result = run_checks(tmp_path, Settings())
    names = {c["name"] for c in result["checks"]}
    assert {"python 版本", "工作区可写", "业务数据集", "依赖 langgraph"} <= names
    assert isinstance(result["ok"], bool)


def test_backup_restore_roundtrip(tmp_path):
    from src.ops.backup import backup_workspace
    from src.harness.state.db import StateDb, verify_backup
    from src.harness.state.queue import JobQueue
    from src.harness.state import states
    workspace = tmp_path / "ws"
    workspace.mkdir()
    db = StateDb(workspace / "state.sqlite")
    job_id = JobQueue(db).submit(request={"task": "备份我"})
    db.close()
    marker = workspace / "jobs" / job_id
    marker.mkdir(parents=True)
    (marker / "job.json").write_text('{"status":"completed"}', encoding="utf-8")
    result = backup_workspace(workspace, tmp_path / "bak")
    manifest = result["manifest"]
    names = {part["name"] for part in manifest["parts"]}
    assert "state.sqlite" in names and "jobs" in names
    backup_db = Path(result["backup_dir"]) / "state.sqlite"
    assert verify_backup(backup_db)
    probe = StateDb(backup_db)
    try:
        assert JobQueue(probe).get(job_id)["status"] == states.QUEUED
    finally:
        probe.close()
    restored = Path(result["backup_dir"]) / "jobs" / job_id / "job.json"
    assert restored.read_text(encoding="utf-8").startswith('{"status"')


def test_verify_fresh_environment(tmp_path):
    from src.ops.verify import run_verify
    result = run_verify()
    names = [c["name"] for c in result["checks"]]
    assert "导入冒烟" in names and "离线 CLI 样例" in names and "健康检查" in names
