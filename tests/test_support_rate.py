# -*- coding: utf-8 -*-
"""批次 C：语义支持率判分脚本的离线解析层（不含模型调用）。"""
import json

from eval.support_rate import (build_pairs, extract_array, extract_citations,
                               pair_id, split_units, unit_kind)


def test_unit_kind_filters_artifacts():
    # 纯引用簇（逗号切分残余）与簿记行不是内容断言，不判分
    assert unit_kind("- 【[E-001]】【[E-002]】【[E-005]】〔推断〕") == "citation_only"
    assert unit_kind("来源定位：粘贴文本1中标【[E-005]】的条目。") == "bookkeeping"
    assert unit_kind("甲单价3150元/台〔事实〕【[E-003]】。") == "assertion"
    assert unit_kind("该队取代了来自奥克兰的新西兰骑士队〔事实〕【[E-003]】。") == "assertion"
    # 短事实句是真断言，不得被引用簇过滤器误杀（回归：阈值曾误设 10）
    assert unit_kind("乙未提供报价【[E-009]】【[E-002]】。") == "assertion"


def test_extract_array_plain_fenced_and_wrapped():
    plain = '[\n  {"id": "a", "verdict": "supported", "reason": "直接支持"}\n]'
    assert extract_array(plain) == [{"id": "a", "verdict": "supported", "reason": "直接支持"}]
    fenced = '```json\n[{"id": "b", "verdict": "partial", "reason": "有引申"}]\n```'
    assert extract_array(fenced)[0]["id"] == "b"
    wrapped = '{"results": [{"id": "c", "verdict": "unsupported", "reason": "无关"}]}'
    assert extract_array(wrapped)[0]["id"] == "c"
    assert extract_array("完全不是 JSON 的回复") is None


def test_split_units_sentences_and_long_comma_split():
    text = ("## 结论\n甲更适合外勤场景〔推断〕【[E-001]】。\n"
            "乙的报价缺失，" + "，".join(["细节" * 20] * 6) + "。【[E-002]】")
    units = split_units(text)
    assert all(len(u) <= 160 for u in units)
    assert any("【[E-001]】" in u for u in units)
    assert any("[E-002]" in u for u in units)


def test_extract_citations_dedup_and_variants():
    unit = "甲单价3150元【[E-003]】，乙重1.9kg [E-004]；（E-003）再引一次【E-005】。"
    assert extract_citations(unit) == ["E-003", "E-004", "E-005"]
    assert extract_citations("没有引用的句子") == []


def test_pair_id_stable():
    assert pair_id("job_a", "断言。", "E-001") == pair_id("job_a", "断言。", "E-001")
    assert pair_id("job_a", "断言。", "E-001") != pair_id("job_a", "断言。", "E-002")


def test_build_pairs_from_report_and_workspace(tmp_path):
    job_id = "job_" + "a" * 32
    job_dir = tmp_path / "workspace" / "jobs" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "evidence.json").write_text(json.dumps({"items": [
        {"evidence_id": "E-001", "source_id": "s1", "fact": "甲单价3150元",
         "quote": "单价3150元/台", "tag": "F"},
        {"evidence_id": "E-009", "source_id": "s1", "fact": "无关", "quote": "无关", "tag": "F"},
    ]}, ensure_ascii=False), encoding="utf-8")
    (job_dir / "job.json").write_text(json.dumps(
        {"final_text": "甲单价3150元【[E-001]】。乙未提供报价【[E-009]】【[E-002]】。"},
        ensure_ascii=False), encoding="utf-8")
    report = tmp_path / "business_report.json"
    report.write_text(json.dumps({"results": [
        {"id": "pc01", "root_job_id": job_id, "final_text_head": "（head 截断不影响）"},
        {"id": "pc02", "root_job_id": "job_" + "b" * 32, "final_text_head": "无证据目录"},
    ]}, ensure_ascii=False), encoding="utf-8")
    pairs, missing, skipped = build_pairs(report)
    ids = {(p["case_id"], p["evidence_id"]) for p in pairs}
    assert ("pc01", "E-001") in ids and ("pc01", "E-009") in ids
    assert all(len(p["quote"]) <= 220 for p in pairs)
    # E-002 不在 evidence.json → 计入 missing；pc02 无 evidence.json → 整例 missing
    assert any(m.get("evidence_id") == "E-002" for m in missing)
    assert any(m.get("case_id") == "pc02" for m in missing)
