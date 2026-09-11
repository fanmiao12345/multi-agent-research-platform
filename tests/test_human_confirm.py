# -*- coding: utf-8 -*-
"""测试：人工确认/覆盖入口与独立评测模型装配（S6-05 ②③）。"""
import csv
import json

from tests._s4_pipeline_brain import S4Brain

from eval.human_scores import build_sheets, ingest_sheets


def _stub_report(tmp_path):
    from eval.business_eval import run_business_eval
    return run_business_eval(workspace_root=tmp_path / "ws", mode="mock",
                             llm=S4Brain(), repeats=1, fault_rounds=1,
                             task_filter="o01", out_dir=tmp_path / "out")


def test_human_ingest_confirms_and_overrides_agent(tmp_path):
    report = _stub_report(tmp_path)
    sheets_dir = tmp_path / "sheets"
    build_sheets(report, sheets_dir)
    # 模拟人工打分：四维 5/4/4/5，改稿 6 分钟
    sheet = next(sheets_dir.glob("*_scores.csv"))
    rows = list(csv.reader(sheet.open(encoding="utf-8-sig")))
    for row in rows:
        key = (row[0] or "").strip()
        if key in ("correctness", "structure", "citations", "completeness"):
            row[2] = "5" if key in ("correctness", "completeness") else "4"
        elif key == "revised_minutes":
            row[2] = "6"
    with sheet.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    out_path = tmp_path / "human_report.json"
    updated = ingest_sheets(report, sheets_dir)
    out_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    record = updated["records"][0]
    assert record["human"]["dimensions"]["correctness"] == 5
    assert record["human"]["revised_minutes"] == 6
    assert record["human"]["human_verdict"] == "accept"
    if "grader" in record:
        assert record["grader"]["human_confirmed"] is True
    summary = updated["meta"]["human_confirm"]
    assert summary["applied"] == 1 and summary["accept_human"] == 1


def test_human_ingest_rejects_out_of_range_score(tmp_path):
    report = _stub_report(tmp_path)
    sheets_dir = tmp_path / "sheets"
    build_sheets(report, sheets_dir)
    sheet = next(sheets_dir.glob("*_scores.csv"))
    rows = list(csv.reader(sheet.open(encoding="utf-8-sig")))
    for row in rows:
        if (row[0] or "").strip() == "structure":
            row[2] = "9"
    with sheet.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    import pytest
    with pytest.raises(ValueError, match="越界"):
        ingest_sheets(report, sheets_dir)


def test_grader_model_config_resolution(monkeypatch):
    from eval.grader import build_grader_llm
    from config.settings import Settings
    # 无 GRADER_MODEL_NAME → 使用主模型（同模型局限由调用方记录）
    monkeypatch.delenv("GRADER_MODEL_NAME", raising=False)
    llm, used = build_grader_llm("mock")
    assert used == "deepseek-chat" or used  # mock 档任意模型名
    # 显式 model 优先
    monkeypatch.setenv("GRADER_MODEL_NAME", "deepseek-v4-pro")
    _, used_env = build_grader_llm("mock")
    assert used_env == "deepseek-v4-pro"
    _, used_arg = build_grader_llm("mock", model="deepseek-v4-flash")
    assert used_arg == "deepseek-v4-flash"


def test_combined_sheet_roundtrip(tmp_path):
    """consolidate 合并总表：填一处即可导入；combined 存在时优先于逐例表。"""
    from eval.human_scores import build_combined_sheet
    report = _stub_report(tmp_path)
    workbench = tmp_path / "workbench"
    build_combined_sheet([(report, None)], workbench)
    combined = workbench / "combined_scores.csv"
    assert combined.exists()
    rows = list(csv.reader(combined.open(encoding="utf-8-sig", newline="")))
    header = next(r for r in rows if r and r[0] == "case_id")
    idx = {name: i for i, name in enumerate(header)}
    data = [r for r in rows if r and r[idx["case_id"]] not in ("", "case_id")
            and not r[0].startswith("#")]
    assert len(data) == 1
    data[0][idx["correctness"]] = "5"
    data[0][idx["structure"]] = "3"       # 有一维 <4 → verdict 应为 draft
    data[0][idx["revised_minutes"]] = "9"
    with combined.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    updated = ingest_sheets(report, workbench)   # 目录里没有逐例表，只有 combined
    record = updated["records"][0]
    assert record["human"]["dimensions"]["structure"] == 3
    assert record["human"]["revised_minutes"] == 9
    assert record["human"]["human_verdict"] == "draft"
    assert updated["meta"]["human_confirm"]["applied"] == 1
