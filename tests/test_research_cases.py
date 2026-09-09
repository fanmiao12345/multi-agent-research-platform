from eval.research_cases import load_dataset, validate_dataset


def test_business_dataset_has_supported_evidence_and_no_fake_passes():
    data = load_dataset()
    assert validate_dataset(data) == []
    assert len(data["tasks"]) == 20 and len(data["faults"]) == 10
    assert {t["expected_outcome"] for t in data["tasks"]} == {"final", "draft", "unable"}


def test_rejects_fabricated_quote_and_disallowed_source():
    data = load_dataset()
    data["tasks"][0]["facts"][0]["quote"] = "本研究已证明因果"
    assert any("引文" in e for e in validate_dataset(data))
    data["tasks"][0]["facts"][0]["source_id"] = "s12"
    assert any("允许来源" in e for e in validate_dataset(data))


def test_rejects_withdrawn_evidence_and_false_execution_status():
    data = load_dataset()
    data["tasks"][-1]["source_ids"].append("s02")
    data["tasks"][0]["status"] = "passed"
    errors = validate_dataset(data)
    assert any("撤回" in e for e in errors)
    assert any("not_run" in e for e in errors)
