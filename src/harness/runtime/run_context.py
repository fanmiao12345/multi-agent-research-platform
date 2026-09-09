# -*- coding: utf-8 -*-
"""
harness/runtime/run_context.py —— RuntimeContext（DEV_PLAN 3.2 / 步骤 19）

保存一次 Run 的静态运行参数（user_id / model_profile / max_iterations / max_cost /
permissions / workspace_path / trace_level）。这些数据不会作为对话消息反复传给模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math

from config.settings import Settings

# 内置工具（M0 注册表）默认全放行；后续 Tool Permission 按风险分级（D4/H8）
DEFAULT_PERMISSIONS = frozenset({"calculator", "current_time"})


@dataclass(frozen=True)
class RuntimeContext:
    settings: Settings = field(default_factory=Settings)
    user_id: str = "local"
    model_profile: str = "default"          # Milestone 9 起支持 fast/balanced/deep…
    max_iterations: int = 5
    max_cost: float | None = None           # 累计估算美元成本停止阈值；None = 不限
    permissions: frozenset = DEFAULT_PERMISSIONS
    workspace_path: Path = field(default_factory=lambda: Settings().workspace_dir)
    trace_level: str = "INFO"

    def __post_init__(self):
        if isinstance(self.max_iterations, bool) or not isinstance(self.max_iterations, int) \
                or self.max_iterations < 1:
            raise ValueError("max_iterations 必须是正整数")
        if self.max_cost is not None and (isinstance(self.max_cost, bool)
                or not isinstance(self.max_cost, (int, float))
                or not math.isfinite(self.max_cost) or self.max_cost < 0):
            raise ValueError("max_cost 必须是非负有限数或 None")

    @classmethod
    def from_settings(cls, settings: Settings | None = None, **overrides) -> "RuntimeContext":
        s = settings or Settings()
        kwargs = {
            "settings": s,
            "max_iterations": 5,
            "workspace_path": s.workspace_dir,
            "trace_level": s.trace_level,
        }
        kwargs.update(overrides)
        return cls(**kwargs)

    def with_updates(self, **overrides) -> "RuntimeContext":
        """返回一个只改部分字段的新 Context（不可变对象惯例）。"""
        base = {
            "settings": self.settings, "user_id": self.user_id,
            "model_profile": self.model_profile,
            "max_iterations": self.max_iterations, "max_cost": self.max_cost,
            "permissions": self.permissions, "workspace_path": self.workspace_path,
            "trace_level": self.trace_level,
        }
        base.update(overrides)
        return RuntimeContext(**base)
