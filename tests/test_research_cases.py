from eval.research_cases import load_dataset, validate_dataset


def test_business_dataset_has_supported_evidence_and_no_fake_passes():
    data = load_dataset()
    assert validate_dataset(data) == []
    assert len(data["tasks"]) == 33 and len(data["faults"]) == 10   # v2：20 冻结 + 13 扩充
    assert data["meta"]["version"] == 2
    v1 = [t for t in data["tasks"] if t["batch"] == "v1"]
    v2 = [t for t in data["tasks"] if t["batch"] == "v2"]
    assert len(v1) == 20 and len(v2) == 13                          # 冻结分母不被扩充改动
    assert {t["expected_outcome"] for t in data["tasks"]} == {"final", "draft", "unable"}
    assert all(t.get("mechanisms") for t in v2)                     # v2 全部带机制标签


def test_rejects_fabricated_quote_and_disallowed_source():
    data = load_dataset()
    data["tasks"][0]["facts"][0]["quote"] = "本研究已证明因果"
    assert any("引文" in e for e in validate_dataset(data))
    data["tasks"][0]["facts"][0]["source_id"] = "s12"
    assert any("允许来源" in e for e in validate_dataset(data))


def test_rejects_withdrawn_evidence_and_false_execution_status():
    data = load_dataset()
    withdrawn = next(t for t in data["tasks"] if t.get("withdrawn_source_ids"))
    withdrawn["source_ids"].append(withdrawn["withdrawn_source_ids"][0])
    data["tasks"][0]["status"] = "passed"
    errors = validate_dataset(data)
    assert any("撤回" in e for e in errors)
    assert any("not_run" in e for e in errors)


def test_v3_public_dataset_validates_and_keeps_v1_frozen():
    """批次 A（docs/PUBLIC_DATASETS_CANDIDATES.md §四）：公开数据转换集独立成文件。

    v1/v2 冻结不动（上面已断言 33+10/version=2）；v3 独立文件 30 例
    （CMRC 20 answerable + DuReader_robust unanswerable 10），quote 逐字
    命中由 validate_dataset 复核，出处/许可证记 meta.provenance。
    """
    v3 = load_dataset("eval/datasets/research_writing_v3_public.json")
    assert validate_dataset(v3) == []
    assert len(v3["tasks"]) == 30 and v3["meta"]["version"] == 3
    assert all(t["batch"] == "v3" for t in v3["tasks"])
    cmrc = [t for t in v3["tasks"] if t["id"].startswith("pc")]
    dureader = [t for t in v3["tasks"] if t["id"].startswith("pu")]
    assert len(cmrc) == 20 and len(dureader) == 10
    assert all(t["expected_outcome"] == "final" and t["mechanisms"] == ["evidence_location"]
               for t in cmrc)
    assert all(t["expected_outcome"] == "unable"
               and t["mechanisms"] == ["refuse_without_evidence"] and t["gaps"]
               for t in dureader)
    # 逐例 quote 确在来源原文（与 validate_dataset 双保险，锁定转换质量）
    by_id = {s["id"]: s["text"] for s in v3["sources"]}
    assert all(f["quote"] in by_id[f["source_id"]]
               for t in v3["tasks"] for f in t["facts"])


def test_load_dataset_accepts_custom_path():
    data = load_dataset("eval/datasets/research_writing_v3_public.json")
    assert data["meta"]["name"] == "research_writing_v3_public"
