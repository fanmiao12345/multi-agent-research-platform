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
    # v2 起数量以 meta.counts 为准（v1 的 8/8/4 仍是冻结基线子集；扩充不得静默改动）
    counts = data["meta"].get("counts") or {}
    expected_categories = {k: v for k, v in counts.items() if k != "fault"}
    actual_categories = dict(Counter(t["category"] for t in data["tasks"]))
    if expected_categories and actual_categories != expected_categories:
        errors.append(f"业务案例分布与 meta.counts 不符：{actual_categories} vs {expected_categories}")
    expected_faults = counts.get("fault", 10)
    if len(data["faults"]) != expected_faults:
        errors.append(f"故障案例必须为{expected_faults}个")
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
    print(json.dumps({"dataset": dataset["meta"]["name"],
                      "dataset_version": dataset["meta"].get("version", 1),
                      "definition_valid": not errors,
                      "business_tasks": len(dataset["tasks"]), "fault_cases": len(dataset["faults"]),
                      "v1_frozen_baseline": len(dataset["meta"].get("v1_baseline",
                                                                    {}).get("frozen_ids", [])),
                      "execution_status": "实际执行状态见 docs/EXECUTION_STATUS.md；"
                                          "本命令只校验案例定义，不运行业务流程",
                      "errors": errors}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
