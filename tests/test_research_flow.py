# -*- coding: utf-8 -*-
"""测试：ResearchApplication/CLI/Web 的研究写作链接入（B5-07）。"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.application.request import TaskRequest
from src.application.research import ResearchApplication
from src.llm.base import ChatResult


class FlowBrain:
    """可跑完整链的桩大脑（模式与 test_pipeline_stages.PipelineBrain 相同但独立）。"""
    model_name = "flow-stub"
    run_mode = "mock"
    provider = "stub"

    def __init__(self):
        self.purposes = []
        self._draft_count = 0

    def chat(self, messages, tools=None):
        system = next(m["content"] for m in messages if m["role"] == "system")
        user = next((m["content"] or "") for m in messages if m["role"] == "user")
        import re
        for marker, name in (("证据提取器", "evidence"), ("素材整理器", "material"),
                             ("提纲规划器", "outline"), ("报告写作者", "draft"),
                             ("审校员", "review")):
            if marker in system:
                purpose = name
                break
        else:
            purpose = "unknown"
        self.purposes.append(purpose)
        return ChatResult(content=self._reply(purpose, user),
                          usage={"prompt_tokens": 15, "completion_tokens": 6})

    def _reply(self, purpose, user):
        import json as _json
        if purpose == "evidence":
            body = user.split("---- 来源全文开始 ----", 1)[-1].split("---- 来源全文结束 ----", 1)[0]
            items = []
            for line in body.splitlines():
                if line.strip() and not line.strip().startswith("#"):
                    quote = line.strip()[:30]
                    items.append({"fact": f"要点：{quote}…", "tag": "F", "quote": quote})
            return _json.dumps({"items": items[:2]}, ensure_ascii=False)
        ids = list(dict.fromkeys(__import__("re").findall(r"E-\d{3}", user)))
        if purpose == "material":
            return _json.dumps({"topics": [{"name": "主题甲", "points": [
                {"evidence_id": i, "statement": f"陈述{i}"} for i in ids]}],
                "conflicts": [], "gaps": []}, ensure_ascii=False)
        if purpose == "outline":
            return _json.dumps({"title": "流报告", "sections": [
                {"heading": "背景", "required_evidence": [ids[0]],
                 "require_fact_markers": True},
                {"heading": "结论", "required_evidence": [ids[1]]}]}, ensure_ascii=False)
        if purpose == "draft":
            self._draft_count += 1
            lines = ["# 流报告", ""]
            for match in __import__("re").finditer(
                    r"^([^：\n]+)：([^\n]*?)；必须覆盖证据 ([^；\n]+)(；需事实/推断标注)?",
                    user, __import__("re").MULTILINE):
                heading, purpose_text, required_raw, markers = (
                    match.group(1), match.group(2), match.group(3), match.group(4))
                lines.append(f"## {heading.strip()}")
                if purpose_text.strip():
                    lines.append(f"本节目的：{purpose_text.strip()}")
                for req in [x.strip() for x in required_raw.split(",") if x.strip()]:
                    mark = "〔事实〕" if markers else ""
                    lines.append(f"支持见 [{req}]{mark}。")
                if markers:
                    lines.append("另注〔推断〕边界。")
                lines.append("")
            return _json.dumps({"report_markdown": "\n".join(lines)}, ensure_ascii=False)
        return _json.dumps({"issues": [], "verdict": "accepted"})  # review


def test_request_flow_validation():
    with pytest.raises(ValueError):
        TaskRequest("t", flow="other")
    request = TaskRequest.from_payload({"task": "t", "flow": "research"})
    assert request.flow == "research"
    assert TaskRequest("t").flow == "agent"


def test_request_hard_requirement_validation():
    with pytest.raises(ValueError):
        TaskRequest("t", required_sections=("",))
    with pytest.raises(ValueError):
        TaskRequest("t", forbidden_claims=("x" * 201,))
    with pytest.raises(ValueError):
        TaskRequest("t", key_facts=tuple(f"f{i}" for i in range(41)))
    request = TaskRequest.from_payload({"task": "t",
                                        "required_sections": ["资料目录", "覆盖范围"]})
    assert request.required_sections == ("资料目录", "覆盖范围")
    assert TaskRequest("t", key_facts="单个事实").key_facts == ("单个事实",)
    assert request.snapshot()["required_sections"] == ("资料目录", "覆盖范围")


def test_application_passes_hard_requirements_into_chain(tmp_path):
    """任务硬约束经统一入口进入链：请求快照、pipeline 快照与复验读数都可查。"""
    request = TaskRequest("写一份带引用的整理报告", flow="research",
                          texts=("第一段正文：A 是 42。\n\n第二段正文：B 可验证。\n",),
                          required_sections=("资料目录", "覆盖范围"),
                          forbidden_claims=("全体满意",),
                          key_facts=("A 是 42",))
    result = ResearchApplication(request, llm=FlowBrain(), workspace_root=tmp_path).run()
    assert result.draft_level == "accepted"
    assert result.hard_checks["required_sections_total"] == 2
    assert result.hard_checks["required_section_hits"] == 2
    assert result.hard_checks["forbidden_hits"] == 0
    job_dir = tmp_path / "jobs" / result.root_job_id
    saved = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
    assert saved["required_sections"] == ["资料目录", "覆盖范围"]
    pipeline = json.loads((job_dir / "pipeline.json").read_text(encoding="utf-8"))
    assert pipeline["hard_requirements"]["required_sections"] == ["资料目录", "覆盖范围"]
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert job["pipeline"]["hard_checks"]["required_section_hits"] == 2


def test_application_research_flow_end_to_end(tmp_path):
    material = tmp_path / "材料.md"
    material.write_text("第一段正文：A 是 42。\n\n第二段正文：B 可验证。\n", encoding="utf-8")
    request = TaskRequest("写一份带引用的整理报告", flow="research",
                          texts=("补充粘贴：C 与 D。\n",), files=(str(material),))
    brain = FlowBrain()
    result = ResearchApplication(request, llm=brain, workspace_root=tmp_path).run()
    assert result.draft_level == "accepted"
    assert result.termination_reason == "success"
    assert result.final_text.startswith("# 流报告")
    job_dir = tmp_path / "jobs" / result.root_job_id
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert job["status"] == "completed"
    assert job["import"]["usable"] == 2
    assert job["pipeline"]["draft_level"] == "accepted"
    assert "draft" in brain.purposes and "review" in brain.purposes
    # 账本记录每个阶段用途，正文不出现在 request.json
    ledger = json.loads((job_dir / "ledger.json").read_text(encoding="utf-8"))
    purposes = {c["purpose"] for c in ledger["calls"]}
    assert {"evidence_extract", "material_pack", "outline", "draft", "review"} <= purposes
    request_saved = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
    assert request_saved["flow"] == "research"
    assert "补充粘贴" not in json.dumps(request_saved, ensure_ascii=False)
    # 引用可全部定位到 evidence.json
    report = result.final_text
    items = json.loads((job_dir / "evidence.json").read_text(encoding="utf-8"))["items"]
    from src.application.pipeline.evidence import collect_citations
    assert set(collect_citations(report)) <= {i["evidence_id"] for i in items}


def test_application_research_flow_without_sources_has_clear_result(tmp_path):
    request = TaskRequest("写主题报告", flow="research", max_calls=5)
    brain = FlowBrain()
    result = ResearchApplication(request, llm=brain, workspace_root=tmp_path).run()
    assert result.draft_level == "draft"
    assert result.termination_reason == "incomplete"
    assert "没有可用资料" in result.message
    assert brain.purposes == []  # 没有可读资料就不发模型请求
    job_dir = tmp_path / "jobs" / result.root_job_id
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert job["status"] == "partial"


def test_cli_research_flow_messages_and_exit_codes(tmp_path):
    cli_root = tmp_path / "cli"
    proc = subprocess.run(
        [sys.executable, "-m", "src.interfaces.cli", "写主题报告", "--flow", "research",
         "--workspace", str(cli_root)],
        capture_output=True, text=True, encoding="utf-8",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=30)
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["termination_reason"] == "incomplete"
    assert "没有可用资料" in payload.get("message", "")
    # 无效 flow 是用法错误（退出码2）
    proc2 = subprocess.run(
        [sys.executable, "-m", "src.interfaces.cli", "x", "--flow", "nope",
         "--workspace", str(cli_root)],
        capture_output=True, text=True, encoding="utf-8",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=30)
    assert proc2.returncode == 2


def test_workbench_state_queues_research_flow(tmp_path):
    from src.interfaces.web.workbench import WorkbenchState
    state = WorkbenchState(tmp_path)
    try:
        info = state.start_run({"task": "写报告", "flow": "research", "max_calls": 0})
        assert info["status"] == "queued" and info["job_id"].startswith("job_")
        row = state.queue.get(info["job_id"])
        # worker 可能已立即领取（running）甚至跑完（failed，默认 Mock 不能产出链式 JSON）
        assert row["kind"] == "research"
        assert row["status"] in ("queued", "running", "failed", "cancelled")
    finally:
        state.stop_worker()
