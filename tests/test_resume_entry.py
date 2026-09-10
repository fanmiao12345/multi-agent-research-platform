# -*- coding: utf-8 -*-
"""测试：S4 续跑入口（resume_research_job / CLI --resume-job）。"""
import json
import os
import subprocess
import sys

import pytest

from src.application.request import TaskRequest
from src.application.research import ResearchApplication, resume_research_job
from src.harness.storage.sources import SourceImportError
from tests._s4_pipeline_brain import S4Brain


class CrashAfterMaterial(S4Brain):
    def _reply(self, purpose, user):
        if purpose == "outline":
            raise RuntimeError("模拟进程中断（提纲阶段）")
        return super()._reply(purpose, user)


def _seed_failed_job(tmp_path, **request_kwargs):
    material = tmp_path / "材料.md"
    material.write_text("第一段：甲方案 42%。\n\n第二段：乙方法可验证。\n", encoding="utf-8")
    request = TaskRequest("写带引用的整理报告", flow="research",
                          files=(str(material),), **request_kwargs)
    with pytest.raises(RuntimeError, match="模拟进程中断"):
        ResearchApplication(request, llm=CrashAfterMaterial(),
                            workspace_root=tmp_path).run()
    job_dir = next((tmp_path / "jobs").glob("job_*"))
    return job_dir.name


def test_resume_research_job_continues_with_continuation_ledger(tmp_path):
    job_id = _seed_failed_job(tmp_path)
    result = resume_research_job(workspace_root=tmp_path, job_id=job_id,
                                 llm=S4Brain())
    assert result.draft_level == "accepted"
    assert result.root_job_id == job_id
    job_dir = tmp_path / "jobs" / job_id
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert job["status"] == "completed"
    assert job["resume"] is True and job["pipeline"]["draft_level"] == "accepted"
    # 续跑账本：新账本目录存在并含恢复阶段的调用（不含重复的证据抽取）
    continuation = tmp_path / "jobs" / job["continuation_ledger"]
    ledger = json.loads((continuation / "ledger.json").read_text(encoding="utf-8"))
    purposes = {c["purpose"] for c in ledger["calls"]}
    # 证据只抽取过一次（原任务那次）；恢复不重新抽取
    evidence_calls = [c for c in ledger["calls"] if c["purpose"] == "evidence_extract"]
    assert len(evidence_calls) == 1
    assert {"outline", "draft", "review"} <= purposes
    # 原账本调用被载入续接（次数延续，不归零）
    assert ledger["call_count"] >= 3


def test_resume_keeps_hard_requirements(tmp_path):
    """续跑不丢任务硬约束：快照恢复必需章节，复验读数与首次运行同口径。"""
    job_id = _seed_failed_job(tmp_path, required_sections=("资料目录", "覆盖范围"))
    result = resume_research_job(workspace_root=tmp_path, job_id=job_id, llm=S4Brain())
    assert result.draft_level == "accepted"
    assert result.hard_checks["required_sections_total"] == 2
    assert result.hard_checks["required_section_hits"] == 2
    job_dir = tmp_path / "jobs" / job_id
    saved = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
    assert saved["required_sections"] == ["资料目录", "覆盖范围"]
    pipeline = json.loads((job_dir / "pipeline.json").read_text(encoding="utf-8"))
    assert pipeline["hard_requirements"]["required_sections"] == ["资料目录", "覆盖范围"]


def test_resume_research_job_missing_or_corrupt_is_explicit(tmp_path):
    with pytest.raises(SourceImportError, match="不存在"):
        resume_research_job(workspace_root=tmp_path, job_id="job_zzz",
                            llm=S4Brain())
    job_id = _seed_failed_job(tmp_path)
    request_file = tmp_path / "jobs" / job_id / "request.json"
    request_file.write_text("{broken", encoding="utf-8")
    with pytest.raises(SourceImportError, match="request.json 损坏"):
        resume_research_job(workspace_root=tmp_path, job_id=job_id, llm=S4Brain())


def test_cli_resume_job_flag(tmp_path):
    """CLI 续跑走真实/桩模型（默认 Mock 不能产出链式 JSON，返回明确 failed）。"""
    job_id = _seed_failed_job(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "src.interfaces.cli", "--workspace", str(tmp_path),
         "--resume-job", job_id],
        capture_output=True, text=True, encoding="utf-8",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=120)
    # 默认 Mock：续跑管线真实执行到 outline 阶段因非 JSON 输出明确失败（不假成功）
    assert proc.returncode == 1, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["draft_level"] == "failed"
    assert payload["root_job_id"] == job_id
    # 无效 job：明确错误、退出码1
    proc2 = subprocess.run(
        [sys.executable, "-m", "src.interfaces.cli", "--workspace", str(tmp_path),
         "--resume-job", "job_nope"],
        capture_output=True, text=True, encoding="utf-8",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=30)
    assert proc2.returncode == 1
    assert "不存在" in json.loads(proc2.stdout)["message"]
