# -*- coding: utf-8 -*-
"""Q4-03：最终签收草稿检查，不代替用户签收。"""
from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/"eval"/"reports"/"q4_signoff_draft.json"

def main():
    freeze=json.loads((ROOT/"eval/reports/freeze_manifest.json").read_text(encoding="utf-8"))
    q1=json.loads((ROOT/"eval/reports/q1_offline_regression.json").read_text(encoding="utf-8"))
    fault=json.loads((ROOT/"eval/reports/q1_fault_matrix.json").read_text(encoding="utf-8"))
    browser=json.loads((ROOT/"eval/reports/q1_browser_install.json").read_text(encoding="utf-8"))
    q2_path=ROOT/"eval/reports/q2_summary.json"
    trial_path=ROOT/"eval/reports/q4_trial/trial_log.json"
    q2=json.loads(q2_path.read_text(encoding="utf-8")) if q2_path.exists() else None
    trial=json.loads(trial_path.read_text(encoding="utf-8")) if trial_path.exists() else {"tasks":[]}
    days=len({t.get("date") for t in trial.get("tasks",[])}); tasks=len(trial.get("tasks",[]))
    checks={"freeze":bool(freeze.get("freeze_check",{}).get("ok")),"q1":bool(q1.get("passed")),
            "faults":bool(fault.get("passed")),"browser_install":bool(browser),
            "q2_ready":bool(q2 and q2.get("ready_for_q3")),"trial_7d_20tasks":tasks>=20 and days>=7}
    report={"schema_version":1,"checks":checks,"trial":{"days":days,"tasks":tasks},
            "user_signoff":False,"note":"自动检查不代替用户对最终 1.0 的签收"}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(0 if all(checks.values()) else 1)
if __name__=="__main__":main()