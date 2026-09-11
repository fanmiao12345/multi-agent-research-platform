# -*- coding: utf-8 -*-
"""Q1-02：故障注入矩阵，每类至少两轮。"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "eval" / "reports" / "q1_fault_matrix.json"

CASES = {
    "f01-无效配置": "tests/test_ops_s6.py::test_fault_invalid_config_probe",
    "f02-网络故障": "tests/test_fetcher.py::test_fetch_scheme_and_dns_errors",
    "f03-空正文": "tests/test_url_sources.py::test_add_url_empty_and_oversize",
    "f04-来源冲突": "tests/test_pipeline_stages.py::test_material_stage_validates_ids_and_forces_conflict_open",
    "f05-预算不足": "tests/test_application.py::test_application_zero_budget_never_calls_model",
    "f06-用户取消": "tests/test_d8_integration.py::test_cancel_stops_later_branches",
    "f07-重启恢复": "tests/test_state_s4.py::test_pipeline_real_subprocess_crash_then_resume",
    "f08-重复提交": "tests/test_state_core.py::test_queue_concurrent_claims_single_winner",
    "f09-路径越界": "tests/test_artifacts.py::test_read_relative_path_within_artifacts_and_index",
    "f10-指令注入": "tests/test_url_imports.py::test_web_content_instructions_remain_inert",
    "m01-并发预留": "tests/test_orchestration_s8.py::test_reserve_is_atomic_and_settlement_restores_capacity",
    "m02-子任务取消": "tests/test_d8_integration.py::test_cancel_stops_later_branches",
    "m03-未知操作": "tests/test_state_core.py::test_op_ledger_crash_window_marks_unknown_no_autoreplay",
    "m04-审批过期": "tests/test_state_core.py::test_approval_expiry_and_scope",
}


def run_case(name: str, node: str, round_index: int) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", node, "-q"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return {"name": name, "round": round_index, "node": node,
            "passed": proc.returncode == 0, "returncode": proc.returncode,
            "output_tail": ((proc.stdout or "") + (proc.stderr or ""))[-2000:]}


def main():
    rows = []
    for name, node in CASES.items():
        for round_index in (1, 2):
            rows.append(run_case(name, node, round_index))
    report = {"schema_version": 1,
              "created_at": datetime.now().isoformat(timespec="seconds"),
              "rounds_per_case": 2, "rows": rows,
              "passed": all(row["passed"] for row in rows),
              "failed": [row for row in rows if not row["passed"]],
              "policy": "失败不跳过、不隐藏；每类至少两轮"}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "rows": len(rows),
                      "passed": report["passed"], "failed": len(report["failed"])},
                     ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()