# -*- coding: utf-8 -*-
"""
harness/usage.py —— Usage Accounting（DEV_PLAN I5 雏形 / 步骤 29）

每次 Run 结束时输出 usage.json：按 Run 汇总 LLM 调用、token、耗时、估算成本。
价格表为"估算用"常量（正式定价随时会变，仅用于量级参考；未知模型 cost=None）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

# (input_usd_per_1M, output_usd_per_1M) —— 估算价，标注日期供复核
_ESTIMATED_PRICES: dict[str, tuple[float, float]] = {
    "mock-rule-v1": (0.0, 0.0),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    # 2026-09-09：官方多次下调 Flash 系列价格，此处按最保守档估算
    # （宁高勿低，避免低估真实花费；正式价格请以官方页为准）
    "deepseek-v4-flash": (0.27, 1.10),
    "deepseek-v4-flash-vision-exp": (0.27, 1.10),
    "deepseek-v4-pro": (0.55, 2.19),
}


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    price = _ESTIMATED_PRICES.get(model)
    if price is None:
        return None
    return (prompt_tokens / 1_000_000 * price[0]
            + completion_tokens / 1_000_000 * price[1])


class UsageTracker:
    """就地累计一次 Run 的用量（每个 llm 调用 record 一次）。"""

    def __init__(self, model: str = ""):
        self.model = model
        self.llm_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.latency_seconds = 0.0
        self.calls: list[dict] = []
        self.usage_complete = True

    def record(self, model: str, usage: dict, latency: float = 0.0) -> None:
        self.llm_calls += 1
        if model != "mock-rule-v1" and not all(
                isinstance((usage or {}).get(k), int) and not isinstance(usage[k], bool)
                and usage[k] >= 0 for k in ("prompt_tokens", "completion_tokens")):
            self.usage_complete = False
        prompt = int((usage or {}).get("prompt_tokens") or 0)
        completion = int((usage or {}).get("completion_tokens") or 0)
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.latency_seconds += latency
        self.calls.append({"seq": len(self.calls) + 1, "model": model,
                           "prompt_tokens": prompt, "completion_tokens": completion,
                           "latency": round(latency, 4),
                           "at": time.strftime("%H:%M:%S")})

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def cost_limit_reached(self, max_cost: float | None) -> bool:
        """按已返回的用量检查估算预算；未知定价/缺失用量时禁止继续花费。"""
        if max_cost is None:
            return False
        if max_cost == 0:
            return True
        cost = estimate_cost_usd(self.model, self.prompt_tokens, self.completion_tokens)
        if cost is None or not self.usage_complete:
            raise ValueError("无法可靠估算成本：模型定价未知或响应缺少用量，已停止运行")
        return cost >= max_cost

    def summary(self, run_id: str = "") -> dict:
        cost = estimate_cost_usd(self.model, self.prompt_tokens, self.completion_tokens)
        return {
            "run_id": run_id,
            "model": self.model,
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "usage_complete": self.usage_complete,
            "latency_seconds": round(self.latency_seconds, 4),
            "estimated_cost_usd": round(cost, 6) if cost is not None else None,
            "calls": self.calls,
        }

    def write(self, run_dir: str | Path, run_id: str = "") -> Path:
        path = Path(run_dir) / "usage.json"
        path.write_text(json.dumps(self.summary(run_id), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path
