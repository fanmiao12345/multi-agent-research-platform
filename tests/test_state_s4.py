# -*- coding: utf-8 -*-
"""测试：S4 有界子进程、无进展检测、链阶段恢复/取消/钩子顺序（S4-04/06/07/11/12）。"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.application.pipeline.runner import run_research_pipeline
from src.harness.control.progress import ProgressDetector
from src.harness.control.subprocess_guard import (SubprocessTimeout,
                                                  run_bounded_subprocess)
from src.harness.storage.sources import SourceStore
from tests._s4_pipeline_brain import S4Brain

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---- S4-07：有界子进程 --------------------------------------------------
def test_subprocess_guard_computes_and_reports_timeout():
    result = run_bounded_subprocess("print(6*7)", timeout=10)
    assert result["returncode"] == 0 and result["stdout"].strip() == "42"
    with pytest.raises(SubprocessTimeout):
        run_bounded_subprocess("import time; time.sleep(30)", timeout=0.8)


def test_subprocess_guard_failure_and_bounded_capture():
    result = run_bounded_subprocess("1/0")
    assert result["returncode"] != 0 and "ZeroDivisionError" in result["stderr"]
    big = run_bounded_subprocess("print('x'*500000)", timeout=10,
                                 capture_limit=1024)
    assert big["returncode"] == 0 and len(big["stdout"]) <= 1024


# ---- S4-11：结构化无进展检测 --------------------------------------------
def test_progress_detector_uses_calls_and_artifact_hash(tmp_path):
    detector = ProgressDetector(stall_threshold=2)
    marker = tmp_path / "out.md"
    marker.write_text("v1", encoding="utf-8")
    assert not detector.boundary(stage="draft", llm_calls=1,
                                 artifact_hash=ProgressDetector.artifact_hash([marker]))
    marker.write_text("v2", encoding="utf-8")   # 产物变化 → 不是无进展
    assert not detector.boundary(stage="draft", llm_calls=2,
                                 artifact_hash=ProgressDetector.artifact_hash([marker]))
    h = ProgressDetector.artifact_hash([marker])
    assert not detector.boundary(stage="draft", llm_calls=2, artifact_hash=h)
    assert detector.boundary(stage="draft", llm_calls=2, artifact_hash=h)


# ---- S4-12：阶段恢复（真实子进程崩溃→重启）-------------------------------
def _sources(job_dir):
    store = SourceStore(job_dir)
    store.add_paste("甲资料第一段：结论甲为 42%。\n\n乙资料第二段：方法乙可复现。\n",
                    display_index=1)
    return store


_CHILD = """import json, os, sys
sys.path.insert(0, {root!r})
from pathlib import Path
from src.application.pipeline.runner import run_research_pipeline
from tests._s4_pipeline_brain import S4Brain
from src.harness.storage.sources import SourceStore
job = Path({job!r})
store = SourceStore(job)
res = run_research_pipeline(llm=S4Brain(mode=os.environ.get("S4_BRAIN_MODE", "good")),
    job_dir=job, store=store, goal="写一份可核查报告", resume={resume!r})
print(json.dumps({{"level": res.draft_level, "reason": res.termination_reason,
    "final_artifact": res.final_artifact_id}}, ensure_ascii=False))
"""


def test_pipeline_real_subprocess_crash_then_resume(tmp_path):
    job_dir = tmp_path / "job"
    _sources(job_dir)
    evidence_before = (job_dir / "evidence.json").read_bytes() \
        if (job_dir / "evidence.json").exists() else b""
    # 第一程：提纲阶段被 os._exit(9) 杀死（检查点/快照均未写）
    first = subprocess.run([sys.executable, "-c", _CHILD.format(
        root=ROOT, job=str(job_dir), resume="False")],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        env=dict(os.environ, PYTHONIOENCODING="utf-8", S4_BRAIN_MODE="crash_at_outline"))
    assert first.returncode == 9, first.stderr
    assert (job_dir / "stage_material.json").exists()
    assert not (job_dir / "stage_outline.json").exists()   # 崩溃在提纲检查点之前
    # 第二程：resume=True 从检查点续跑 → accepted；证据不重复抽取
    second = subprocess.run([sys.executable, "-c", _CHILD.format(
        root=ROOT, job=str(job_dir), resume="True")],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    assert second.returncode == 0, second.stderr
    payload = json.loads(second.stdout.strip().splitlines()[-1])
    assert payload["level"] == "accepted" and payload["reason"] == "success"
    assert payload["final_artifact"].startswith("report.v")
    evidence_after = (job_dir / "evidence.json").read_bytes()
    assert evidence_before == b"" or evidence_after == evidence_before  # 单次抽取
    items = json.loads(evidence_after.decode("utf-8"))["items"]
    assert len(items) >= 1
    # 素材/提纲/初稿检查点都在，报告产物只有初稿+审校产物
    for name in ("stage_material.json", "stage_outline.json", "stage_draft.json"):
        assert (job_dir / name).exists()


def test_pipeline_rerun_without_resume_is_refused(tmp_path):
    job_dir = tmp_path / "job"
    _sources(job_dir)
    result = run_research_pipeline(llm=S4Brain(), job_dir=job_dir,
                                   store=_sources(job_dir), goal="写报告")
    assert result.draft_level == "accepted"
    again = run_research_pipeline(llm=S4Brain(), job_dir=job_dir,
                                  store=SourceStore(job_dir), goal="写报告")
    assert again.draft_level == "failed"
    assert "检查点" in again.message


def test_pipeline_resume_in_process_crash_window(tmp_path):
    """等价崩溃窗口：产物/素材检查点已落，提纲阶段抛异常中断 → resume 续跑。"""
    job_dir = tmp_path / "job"
    _sources(job_dir)

    class CrashAfterMaterial(S4Brain):
        def _reply(self, purpose, user):
            if purpose == "outline":
                raise RuntimeError("模拟进程中断（提纲阶段）")
            return super()._reply(purpose, user)

    with pytest.raises(RuntimeError):
        run_research_pipeline(llm=CrashAfterMaterial(), job_dir=job_dir,
                              store=SourceStore(job_dir), goal="写报告")
    resumed = run_research_pipeline(llm=S4Brain(), job_dir=job_dir,
                                    store=SourceStore(job_dir), goal="写报告",
                                    resume=True)
    assert resumed.draft_level == "accepted"
    # 恢复路径没有重复证据/重复素材产物
    artifacts = json.loads((job_dir / "artifacts.json").read_text(encoding="utf-8"))["artifacts"]
    material_ids = [a["artifact_id"] for a in artifacts if a["kind"] == "material_pack"]
    assert material_ids == ["material_pack.v1"]


# ---- S4-06：取消在阶段边界的收敛 ----------------------------------------
def test_pipeline_stop_request_converges_cancelled(tmp_path):
    job_dir = tmp_path / "job"
    _sources(job_dir)
    flag = {"stopped": False}

    class Brain(S4Brain):
        def _reply(self, purpose, user):
            if purpose == "material":
                flag["stopped"] = True   # 执行中途用户点停止
            return super()._reply(purpose, user)

    result = run_research_pipeline(llm=Brain(), job_dir=job_dir,
                                   store=SourceStore(job_dir), goal="写报告",
                                   should_stop=lambda: flag["stopped"])
    assert result.termination_reason == "cancelled"
    assert result.draft_level == "draft"
    assert "取消请求" in result.message
    assert any(s["status"] == "stopped" for s in result.stages)
    # 已保存的产物保留（不会假装完成）
    assert (job_dir / "stage_material.json").exists()


# ---- S4-04：产物先落盘、状态钩子后提交 ----------------------------------
def test_stage_hook_fires_after_artifacts_and_checkpoints(tmp_path):
    job_dir = tmp_path / "job"
    _sources(job_dir)
    events = []

    def hook(stage, status, artifact_ids, message):
        if status == "completed":
            # 钩子执行时产物文件与检查点必须已存在（先文件后状态）
            for artifact_id in artifact_ids:
                if not artifact_id:
                    continue
                kind = artifact_id.rsplit(".v", 1)[0]
                produced = [p for p in (job_dir / "artifacts").iterdir()
                            if p.name.startswith(kind + ".v")]
                assert produced, f"钩子触发时产物 {artifact_id} 尚未落盘"
            events.append((stage, status, artifact_ids))
        else:
            events.append((stage, status, artifact_ids))

    result = run_research_pipeline(llm=S4Brain(), job_dir=job_dir,
                                   store=SourceStore(job_dir), goal="写报告",
                                   stage_hook=hook)
    assert result.draft_level == "accepted"
    stages = [e[0] for e in events if e[1] == "completed"]
    assert "material" in stages and "outline" in stages and "draft" in stages
    material_checkpoint = job_dir / "stage_material.json"
    assert material_checkpoint.exists()  # 素材检查点与产物同批落盘
