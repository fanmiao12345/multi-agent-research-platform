# -*- coding: utf-8 -*-
"""
eval/evaluators/memory_metrics.py —— Memory Eval（DEV_PLAN F6 / 步骤 73）

给定“检索函数 + 若干查询与期望命中 id 集”，计算：
    Recall Precision / Recall Rate / Irrelevant Injection Rate / Stale Rate
（Wrong Memory 与 Cross-thread Rate 由场景构造，见 tests/test_memory.py）
"""

from __future__ import annotations


def evaluate(retrieve_fn, cases: list[dict]) -> dict:
    """cases: [{query, expected_ids: [id...], stale_ids: [id...]}]"""
    totals = {"precision_hits": 0, "returned": 0, "expected": 0,
              "irrelevant": 0, "stale_returned": 0}
    for case in cases:
        hits = retrieve_fn(case["query"])          # 返回 [(id, ...)] 或对象列表
        ids = [h[0] if isinstance(h, tuple) else getattr(h, "id", h) for h in hits]
        expected = set(case.get("expected_ids", []))
        stale = set(case.get("stale_ids", []))
        returned = set(ids)
        totals["returned"] += len(returned)
        totals["expected"] += len(expected)
        totals["precision_hits"] += len(returned & expected)
        totals["irrelevant"] += len(returned - expected - stale)
        totals["stale_returned"] += len(returned & stale)

    out = {
        "recall_precision": (totals["precision_hits"] / totals["returned"]
                             if totals["returned"] else None),
        "recall_rate": (totals["precision_hits"] / totals["expected"]
                        if totals["expected"] else None),
        "irrelevant_injection_rate": (totals["irrelevant"] / totals["returned"]
                                      if totals["returned"] else None),
        "stale_rate": (totals["stale_returned"] / totals["returned"]
                       if totals["returned"] else None),
    }
    return out
