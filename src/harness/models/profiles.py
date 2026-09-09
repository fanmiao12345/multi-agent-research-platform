# -*- coding: utf-8 -*-
"""
harness/models/profiles.py —— Model Profiles（DEV_PLAN I1 / 步骤 97）

档案：provider / model / temperature / max_tokens / cost 元数据 / capability tags。
内置四档：fast / balanced / deep / cheap。数据可随配置覆盖（未来 config JSON 化）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 成本元数据：(输入 $/1M, 输出 $/1M) —— 估算参考价（与 usage.py 表格一致处应同步）
@dataclass(frozen=True)
class ModelProfile:
    name: str
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    temperature: float = 0.7
    max_tokens: int = 2048
    cost_in: float = 0.27
    cost_out: float = 1.10
    tags: tuple = ("chat",)

    def cost_meta(self) -> tuple[float, float]:
        return self.cost_in, self.cost_out


PROFILES: dict[str, ModelProfile] = {
    "fast": ModelProfile(name="fast", temperature=0.3, max_tokens=1024,
                         tags=("chat", "low_latency")),
    "balanced": ModelProfile(name="balanced", temperature=0.7, max_tokens=2048,
                             tags=("chat", "tool_calls")),
    "deep": ModelProfile(name="deep", temperature=0.5, max_tokens=4096,
                         model="deepseek-reasoner",
                         cost_in=0.55, cost_out=2.19,
                         tags=("reasoning", "no_tools")),
    "cheap": ModelProfile(name="cheap", temperature=1.0, max_tokens=1024,
                          cost_in=0.10, cost_out=0.40, tags=("chat", "cheap")),
}

DEFAULT = "balanced"


def get_profile(name: str) -> ModelProfile:
    if name not in PROFILES:
        raise KeyError(f"未知模型档案 {name}，可用：{sorted(PROFILES)}")
    return PROFILES[name]
