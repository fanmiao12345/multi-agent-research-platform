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