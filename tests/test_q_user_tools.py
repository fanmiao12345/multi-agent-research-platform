# -*- coding: utf-8 -*-
"""Q2～Q4 用户自测工具的结构与汇总验证，不执行真实批次。"""
import json
from pathlib import Path

from eval import q3_compare, q4_signoff, q2_summary, trial_log

ROOT=Path(__file__).resolve().parent.parent


def test_web_topic_dataset_covers_all_six_modes():
    data=json.loads((ROOT/"eval"/"datasets"/"web_topics_v1.json").read_text(encoding="utf-8"))
    counts={}
    for case in data["cases"]:
        counts[case["mode"]]=counts.get(case["mode"],0)+1
    for mode in ("single","fixed","manager_worker","fanout","dynamic_team","debate"):
        assert counts.get(mode,0)>=2


def test_q2_summary_and_q3_compare_metrics(tmp_path, monkeypatch):
    business={"records":[
        {"draft_level":"accepted","status":"passed","estimated_cost_usd":0.1,"elapsed_seconds":10,
         "machine_checks":{"citation_tokens":2,"unresolved_citations":0},"unknown_usage_calls":0},
        {"draft_level":"draft","status":"failed","estimated_cost_usd":0.2,"elapsed_seconds":20,
         "machine_checks":{"citation_tokens":1,"unresolved_citations":1},"unknown_usage_calls":1}]}
    web={"rows":[{"returncode":0,"usable_sources":2,"citation_lineage":[{}]}],
         "mode_coverage":{"single":2},"requirements_met":True}
    human={"meta":{"human_confirm":{"graded_human":2}}}
    monkeypatch.setattr(q2_summary,"OUT",tmp_path/"q2.json")
    summary=q2_summary.summarize(business,web,human)
    assert summary["business"]["attempts_total"]==2
    assert summary["business"]["unknown_usage_calls"]==1
    assert summary["ready_for_q3"] is True

    comparison=q3_compare.compare(business,{"records":[
        {"draft_level":"accepted","status":"passed","estimated_cost_usd":0.05,"elapsed_seconds":8},
        {"draft_level":"accepted","status":"passed","estimated_cost_usd":0.08,"elapsed_seconds":9}]})
    assert comparison["delta"]["accepted"]==1
    assert comparison["delta"]["estimated_cost_usd"]<0


def test_trial_log_status_requires_seven_days_and_twenty_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(trial_log,"PATH",tmp_path/"trial.json")
    data={"schema_version":1,"tasks":[]}
    for day in range(7):
        for index in range(3):
            data["tasks"].append({"date":f"2026-09-{day+1:02d}","id":f"t{day}-{index}"})
    status=trial_log.status(data)
    assert status["days"]==7 and status["tasks"]==21 and status["ready"] is True


def test_user_runbook_and_scripts_exist():
    for path in ("docs/Q2_Q4_USER_RUNBOOK.md","scripts/q2_real.ps1","scripts/q2_web.ps1",
                 "scripts/q2_ingest.ps1","scripts/q3_compare.ps1","scripts/q4_trial.ps1",
                 "scripts/q4_signoff.ps1"):
        assert (ROOT/path).exists(), path