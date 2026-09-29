# -*- coding: utf-8 -*-
"""D7-01/02：引用谱系和按任务选择交付类型。"""
import json

from src.application.orchestration.refs import build_root_lineage
from src.application.pipeline.runner import run_research_pipeline
from src.harness.storage.sources import SourceStore
from tests._s4_pipeline_brain import S4Brain


def _store(tmp_path, texts=("alpha 42 beta。", "gamma 7 delta。")):
    store = SourceStore(tmp_path / "job")
    for index, text in enumerate(texts, start=1):
        store.add_paste(text, display_index=index)
    return store


def test_collection_delivery_stops_at_material_pack(tmp_path):
    result = run_research_pipeline(
        llm=S4Brain(), job_dir=tmp_path / "job",
        store=_store(tmp_path), goal="整理资料目录", delivery_kind="collection")
    assert result.delivery_kind == "collection"
    assert result.draft_level == "accepted"
    assert result.final_artifact_id.startswith("collection.v")
    assert "素材包" in result.final_text


def test_analysis_delivery_uses_short_structured_path(tmp_path):
    result = run_research_pipeline(
        llm=S4Brain(), job_dir=tmp_path / "job",
        store=_store(tmp_path), goal="分析对比资料中的结论",
        delivery_kind="analysis")
    assert result.delivery_kind == "analysis"
    assert result.final_artifact_id.startswith("analysis.v")
    assert "## 核心结论" in result.final_text
    assert "## 依据与限制" in result.final_text


def test_report_delivery_keeps_existing_artifacts(tmp_path):
    result = run_research_pipeline(
        llm=S4Brain(), job_dir=tmp_path / "job",
        store=_store(tmp_path), goal="写一份报告", delivery_kind="report")
    assert result.delivery_kind == "report"
    assert result.final_artifact_id.startswith("report.v")


def test_root_lineage_maps_root_evidence_to_child_original_source(tmp_path):
    root = tmp_path / "jobs" / "job_root"
    root.mkdir(parents=True)
    (root / "evidence.json").write_text(json.dumps({"items": [
        {"evidence_id": "E-001", "source_id": "src_child_output",
         "quote": "alpha 42 beta。"}]}, ensure_ascii=False), encoding="utf-8")
    children = [{
        "child_job_id": "job_child",
        "result": {"evidence_refs": [{
            "job_id": "job_child", "evidence_id": "E-004",
            "source_id": "src_local", "quote_head": "alpha 42 beta。",
            "source_job_id": "job_root", "root_source_id": "src_original",
            "source_version": 2, "locator": {"paragraph": 0, "page": 1},
        }]},
    }]
    lineage = build_root_lineage(tmp_path, "job_root", children)
    assert lineage[0]["root_evidence_id"] == "E-001"
    assert lineage[0]["child_evidence_id"] == "E-004"
    assert lineage[0]["original_root_source_id"] == "src_original"
    assert lineage[0]["locator"]["page"] == 1


def test_build_citation_lineage_maps_every_evidence_to_source(tmp_path):
    """O-25 回补：谱系以"证据→原始来源"为基底，每条证据一条，不依赖子任务匹配。"""
    from src.application.orchestration.refs import build_citation_lineage
    job = tmp_path / "jobs" / "job_x"
    job.mkdir(parents=True)
    (job / "evidence.json").write_text(json.dumps({"items": [
        {"evidence_id": "E-001", "source_id": "src_a", "fact": "试点40人",
         "quote": "试点共40人。", "locator": {"paragraph": 0}},
        {"evidence_id": "E-002", "source_id": "src_missing", "fact": "无登记来源",
         "quote": "孤儿引用。", "locator": {}}]}, ensure_ascii=False), encoding="utf-8")
    (job / "sources.json").write_text(json.dumps({"sources": [
        {"source_id": "src_a", "original_address": "https://example.com/a",
         "final_url": "https://example.com/a?final", "title": "示例页",
         "status": "ok", "root_source_id": "", "source_version": 1}]},
        ensure_ascii=False), encoding="utf-8")
    lineage = build_citation_lineage(job)
    assert [e["root_evidence_id"] for e in lineage] == ["E-001", "E-002"]
    assert lineage[0]["original_address"] == "https://example.com/a"
    assert lineage[0]["final_url"].endswith("?final")
    assert lineage[0]["source_title"] == "示例页"
    # 登记缺失的来源不编造：条目保留，来源字段如实留空
    assert lineage[1]["original_address"] == ""
    # 无子任务时中间溯源字段为空
    assert lineage[0]["child_evidence_id"] == ""


def test_build_citation_lineage_fills_child_provenance_when_matched(tmp_path):
    from src.application.orchestration.refs import build_citation_lineage
    job = tmp_path / "jobs" / "job_root"
    job.mkdir(parents=True)
    (job / "evidence.json").write_text(json.dumps({"items": [
        {"evidence_id": "E-001", "source_id": "src_shared",
         "quote": "alpha 42 beta。"}]}, ensure_ascii=False), encoding="utf-8")
    children = [{"child_job_id": "job_child",
                 "result": {"evidence_refs": [{
                     "job_id": "job_child", "evidence_id": "E-004",
                     "quote_head": "alpha 42 beta。"}]}}]
    lineage = build_citation_lineage(job, children)
    assert lineage[0]["child_job_id"] == "job_child"
    assert lineage[0]["child_evidence_id"] == "E-004"


def test_app_research_run_writes_citation_lineage_file(tmp_path):
    """O-25：fixed/单任务链路径此前从不写 citation_lineage.json，本测试锁定修复。"""
    from src.application.research import ResearchApplication
    from src.application.request import TaskRequest
    from tests._s4_pipeline_brain import S4Brain
    material = tmp_path / "材料.md"
    material.write_text("试点40人，满意率75%[来源甲]。\n\n没有设置对照组，需注明局限。\n",
                        encoding="utf-8")
    request = TaskRequest("写一份带引用的整理报告", flow="research",
                          texts=("补充：工单时长从10小时降到8小时。\n",),
                          files=(str(material),))
    result = ResearchApplication(request, llm=S4Brain(),
                                 workspace_root=tmp_path).run()
    assert result.draft_level == "accepted"
    job = tmp_path / "jobs" / result.root_job_id
    path = job / "citation_lineage.json"
    assert path.exists(), "研究链完成后必须落盘 citation_lineage.json（O-25 回补）"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    evidence = json.loads((job / "evidence.json").read_text(encoding="utf-8"))["items"]
    assert len(payload["lineage"]) == len(evidence) > 0
    entry = payload["lineage"][0]
    assert entry["root_evidence_id"] == evidence[0]["evidence_id"]
    assert entry["original_address"], "谱系条目必须能回溯到来源登记地址（paste:N 或文件路径）"
    assert entry["locator"] == evidence[0]["locator"]