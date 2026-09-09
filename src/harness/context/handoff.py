# -*- coding: utf-8 -*-
"""
harness/context/handoff.py —— Handoff Context Pack（DEV_PLAN E6 / 步骤 63）

Agent 交接不传全部 conversation，只传结构化的 Handoff Pack：
Task / Goal / Known Facts / Evidence / Progress / Artifacts /
Open Questions / Constraints / Expected Output

同时提供两个隔离工具（E5 Context Isolation）：
- 命名空间(namespace)：给不同 Agent 私有状态，不互相污染
- build_pack：从黑板上限选内容组装 Pack（只取相关的、不取全量）
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HandoffPack:
    goal: str = ""
    task: str = ""
    known_facts: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    progress: str = ""
    artifacts: list = field(default_factory=list)
    open_questions: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    expected_output: str = ""

    def to_text(self) -> str:
        lines = [f"# 交接卡｜目标：{self.goal}",
                 f"- 当前任务：{self.task}"]
        if self.known_facts:
            lines.append("- 已知事实：" + "；".join(self.known_facts[:8]))
        if self.evidence:
            lines.append("- 证据来源：" + "；".join(self.evidence[:8]))
        if self.progress:
            lines.append(f"- 进度：{self.progress}")
        if self.artifacts:
            lines.append("- 已有工件：" + "；".join(self.artifacts[:8]))
        if self.open_questions:
            lines.append("- 待解决问题：" + "；".join(self.open_questions[:5]))
        if self.constraints:
            lines.append("- 约束：" + "；".join(self.constraints[:5]))
        if self.expected_output:
            lines.append(f"- 期望产出：{self.expected_output}")
        return "\n".join(lines)


class Namespace:
    """Agent 私有状态区（E5）：隔离 = 默认互不可见，显式共享才可见。"""

    def __init__(self, name: str):
        self.name = name
        self._data: dict = {}

    def put(self, key: str, value) -> None:
        self._data[key] = value

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def snapshot(self) -> dict:
        return dict(self._data)


def build_pack(*, goal: str = "", task: str = "", blackboard: dict | None = None,
               pick: tuple = (), expected_output: str = "") -> HandoffPack:
    """从黑板/工作区里只挑选 pick 指定的键组装 Pack（避免全量搬运）。"""
    blackboard = blackboard or {}
    pack = HandoffPack(goal=goal, task=task, expected_output=expected_output)
    for key in pick:
        value = blackboard.get(key)
        if isinstance(value, list):
            setattr(pack, key, list(value)[:10])
        elif isinstance(value, str) and key in ("progress",):
            pack.progress = value
    return pack
