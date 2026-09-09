"""业务验收材料校验；不调用模型，不把结构有效计为业务通过。"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

DATASET_PATH = Path(__file__).parent / "datasets" / "research_writing_v1.json"


def load_dataset():
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def validate_dataset(data: dict) -> list[str]:
    errors = []
    sources = {s["id"]: s for s in data["sources"]}
    if len(sources) != len(data["sources"]):
        errors.append("来源 ID 重复")
    rows = data["tasks"] + data["faults"]
    if len({r["id"] for r in rows}) != len(rows):
        errors.append("任务 ID 重复")
    if Counter(t["category"] for t in data["tasks"]) != {"organize": 8, "research": 8, "revision": 4}:
        errors.append("业务案例必须为8个整理、8个研究、4个改稿")
    if len(data["faults"]) != 10:
        errors.append("故障案例必须为10个")
    for t in data["tasks"]:
        prefix = t["id"] + ": "
        if not t["request"] or not t["sections"] or not t["facts"] or not t["forbidden_claims"]:
            errors.append(prefix + "缺少需求、章节、事实或禁止项")
        if t["expected_outcome"] not in ("final", "draft", "unable"):
            errors.append(prefix + "交付等级无效")
        if not set(t["source_ids"]) <= sources.keys():
            errors.append(prefix + "引用不存在的来源")
        for fact in t["facts"]:
            source_id = fact["source_id"]
            if source_id not in t["source_ids"] or source_id not in sources:
                errors.append(prefix + "事实引用不在本任务允许来源内")
            elif not fact["quote"] or fact["quote"] not in sources[source_id]["text"]:
                errors.append(prefix + "事实引文与原文不一致")
            if not fact["claim"]:
                errors.append(prefix + "事实断言为空")
        if t["category"] == "revision" and not (t.get("initial_draft") and t.get("revision_of")):
            errors.append(prefix + "改稿缺少原稿或父版本")
        if set(t.get("withdrawn_source_ids", [])) & set(t["source_ids"]):
            errors.append(prefix + "已撤回来源仍被允许使用")
    for fault in data["faults"]:
        if not all(fault.get(k) for k in ("setup", "expected", "stage", "verification")):
            errors.append(fault["id"] + ": 故障缺少注入或验证方式")
    if data["meta"]["execution_status"] != "not_run" or any(r["status"] != "not_run" for r in rows):
        errors.append("案例定义必须保持not_run；实际运行结果应写入独立报告")
    return errors


def main():
    dataset = load_dataset()
    errors = validate_dataset(dataset)
    print(json.dumps({"dataset": dataset["meta"]["name"], "definition_valid": not errors,
                      "business_tasks": len(dataset["tasks"]), "fault_cases": len(dataset["faults"]),
                      "business_executed": 0, "business_passed": None,
                      "status": "not_run", "errors": errors}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
