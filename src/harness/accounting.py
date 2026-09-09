# -*- coding: utf-8 -*-
"""
harness/accounting.py —— Usage / Cost Accounting（DEV_PLAN I5 / 步骤 100-101）

按 Run / Agent / Task / Model / Tool 记录用量与估算成本。
AccountingLedger 与 UsageTracker 的关系：Tracker 负责一次 Run 就地累计，
Ledger 负责跨维度落账（events → 汇总视图）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.harness.models.profiles import PROFILES


def cost_for(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """按档案价格估算美元成本（未知模型返回 None）。"""
    price = None
    for profile in PROFILES.values():
        if profile.model == model or profile.name == model:
            price = profile.cost_meta()
            break
    if price is None:
        return None
    return (prompt_tokens / 1_000_000 * price[0]
            + completion_tokens / 1_000_000 * price[1])


class AccountingLedger:
    def __init__(self, path: str | Path | None = None):
        from config.settings import PROJECT_ROOT

        self.path = Path(path) if path else PROJECT_ROOT / "workspaces" / "usage_ledger.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, *, run_id: str, agent: str = "", task_id: str = "",
               model: str = "", prompt_tokens: int = 0, completion_tokens: int = 0,
               latency: float = 0.0, tool: str = "") -> None:
        entry = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "run_id": run_id, "agent": agent, "task_id": task_id,
            "model": model, "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost_usd": cost_for(model, prompt_tokens, completion_tokens),
            "latency": round(latency, 4), "tool": tool,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def summarize(self) -> dict:
        rows = []
        if not self.path.exists():
            return {"by_dimension": {}, "total_cost_usd": 0.0, "rows": []}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        by: dict[str, dict] = {}
        for r in rows:
            for dim_key, dim_value in (("model", r["model"]), ("agent", r["agent"]),
                                       ("task_id", r["task_id"]), ("run_id", r["run_id"])):
                bucket = by.setdefault(dim_key, {})
                agg = bucket.setdefault(dim_value or "(无)", {"calls": 0, "prompt": 0,
                                                              "completion": 0,
                                                              "cost": 0.0})
                agg["calls"] += 1
                agg["prompt"] += r["prompt_tokens"]
                agg["completion"] += r["completion_tokens"]
                agg["cost"] += r["estimated_cost_usd"] or 0.0
        total_cost = sum(r["estimated_cost_usd"] or 0.0 for r in rows)
        return {"by_dimension": by, "total_cost_usd": round(total_cost, 6), "rows": rows}
