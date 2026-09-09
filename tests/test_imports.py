# -*- coding: utf-8 -*-
"""测试：任务请求携带资料 + ResearchApplication 导入接入（B3-04）。"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.application.request import TaskRequest
from src.application.research import ResearchApplication
from src.harness.storage.sources import SourceImportError, SourceStore
from src.llm.mock import MockLLM


def _file(tmp_path, name="材料.md", text="# 标题\n\n正文内容。\n", encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


# ---- 请求契约 ------------------------------------------------------------
@pytest.mark.parametrize("fields", [
    {"texts": "字符串"}, {"texts": (123,)}, {"files": (True,)},
    {"texts": (None,)}, {"files": ""}, {"texts": tuple(range(21))},
])
def test_request_rejects_bad_source_fields(fields):
    with pytest.raises(ValueError):
        TaskRequest("test", **fields)


def test_request_list_inputs_are_coerced():
    request = TaskRequest("test", texts=["一段。", "二段。"], files=["a.txt", "b.md"])
    assert request.texts == ("一段。", "二段。") and request.files == ("a.txt", "b.md")
    request = TaskRequest.from_payload({"task": "t", "texts": ["x"], "files": ["y"]})
    assert request.texts == ("x",) and request.files == ("y",)
    with pytest.raises(ValueError):
        TaskRequest.from_payload({"task": "t", "texts": {"a": 1}})


def test_request_snapshot_excludes_pasted_content():
    request = TaskRequest("任务", texts=["全文内容不能进快照"], files=["C:/材料.md"])
    snapshot = request.snapshot()
    assert "全文内容不能进快照" not in json.dumps(snapshot, ensure_ascii=False)
    assert snapshot["files"] == ("C:/材料.md",) and snapshot["task"] == "任务"


# ---- 应用导入 ------------------------------------------------------------
def test_application_imports_texts_and_files_before_run(tmp_path):
    material = _file(tmp_path)
    request = TaskRequest("整理这 2 份材料", mode="mock", texts=("粘贴正文。",),
                          files=(str(material),))
    result = ResearchApplication(request, llm=MockLLM(), workspace_root=tmp_path).run()
    job_dir = tmp_path / "jobs" / result.root_job_id
    store = SourceStore(job_dir)
    summary = store.summary()
    assert summary["usable"] == 2 and summary["total"] == 2
    full_texts = {s["kind"]: store.full_text(s["source_id"]) for s in summary["sources"]}
    assert full_texts["paste"] == "粘贴正文。"
    assert full_texts["file"].startswith("# 标题")
    # request.json 不含粘贴正文（全文在 sources/），job.json 记录导入摘要
    request_saved = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
    assert "粘贴正文" not in json.dumps(request_saved, ensure_ascii=False)
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert job["import"] == {"total": 2, "usable": 2, "statuses": {"ok": 2}}
    # 原始资料文件从未被改动
    assert material.read_text(encoding="utf-8").startswith("# 标题")


def test_application_no_usable_source_fails_explicitly(tmp_path):
    request = TaskRequest("整理", texts=("",), files=())
    with pytest.raises(SourceImportError) as exc:
        ResearchApplication(request, llm=MockLLM(), workspace_root=tmp_path).run()
    assert "没有可用资料" in str(exc.value)
    job = next((tmp_path / "jobs").glob("*/job.json"))
    assert json.loads(job.read_text(encoding="utf-8"))["status"] == "failed"


def test_application_mixed_sources_partial_failure_proceeds_with_records(tmp_path):
    missing = tmp_path / "不存在.txt"
    good = _file(tmp_path, "好材料.txt", "可用正文。\n")
    request = TaskRequest("整理", texts=("可用的粘贴。",), files=(str(missing), str(good)))
    result = ResearchApplication(request, llm=MockLLM(), workspace_root=tmp_path).run()
    job_dir = tmp_path / "jobs" / result.root_job_id
    summary = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert summary["import"]["usable"] == 2
    assert summary["import"]["statuses"]["read_failed"] == 1
    records = json.loads((job_dir / "sources.json").read_text(encoding="utf-8"))["sources"]
    failed = [s for s in records if s["status"] == "read_failed"]
    assert failed and "不存在.txt" in failed[0]["display"]


def test_application_too_many_sources_rejected_at_request_layer(tmp_path):
    with pytest.raises(ValueError, match="上限"):
        TaskRequest("t", texts=tuple(f"第{i}份。" for i in range(21)))


def test_duplicate_across_texts_detected_in_application_run(tmp_path):
    request = TaskRequest("整理", texts=("同一份正文。", "同一份正文。"))
    result = ResearchApplication(request, llm=MockLLM(), workspace_root=tmp_path).run()
    job_dir = tmp_path / "jobs" / result.root_job_id
    summary = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert summary["import"]["usable"] == 1
    assert summary["import"]["statuses"]["duplicate"] == 1


def test_cli_import_and_output(tmp_path):
    material = _file(tmp_path, "cli材料.md")
    cli_root = tmp_path / "cli"
    proc = subprocess.run(
        [sys.executable, "-m", "src.interfaces.cli", "整理材料", "--workspace", str(cli_root),
         "--import-file", str(material), "--import-text", "补充粘贴。"],
        capture_output=True, text=True, encoding="utf-8",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=20)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["sources"]["usable"] == 2
    assert (cli_root / "jobs" / payload["root_job_id"] / "sources.json").exists()


def test_cli_zero_usable_import_fails_with_message(tmp_path):
    cli_root = tmp_path / "cli"
    proc = subprocess.run(
        [sys.executable, "-m", "src.interfaces.cli", "整理", "--workspace", str(cli_root),
         "--import-text", "   "],
        capture_output=True, text=True, encoding="utf-8",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=20)
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["error_type"] == "SourceImportError"
    assert "没有可用资料" in payload["message"]
    assert (cli_root / "jobs").exists()
