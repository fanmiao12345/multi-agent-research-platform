# -*- coding: utf-8 -*-
"""
harness/control/progress.py —— 结构化无进展检测（S4-11）

判断依据是结构化调用与产物变化（不是回答里的关键词）：
在阶段边界喂入 (stage, llm_call_count, artifact_hash)，连续相同即无进展。
"""
from __future__ import annotations

import hashlib
import json


class ProgressDetector:
    def __init__(self, stall_threshold: int = 2):
        self.stall_threshold = stall_threshold
        self._last: dict | None = None
        self._stall_count = 0

    @staticmethod
    def artifact_hash(paths: list) -> str:
        digest = hashlib.sha1()
        for path in paths:
            try:
                data = path.read_bytes()
            except OSError:
                data = b""
            digest.update(path.name.encode("utf-8"))
            digest.update(data)
        return digest.hexdigest()

    def boundary(self, *, stage: str, llm_calls: int,
                 artifact_hash: str = "") -> bool:
        """返回 True=无进展（连续多轮相同且没有新调用/新产物）。"""
        key = (stage, llm_calls, artifact_hash)
        if self._last == key:
            self._stall_count += 1
        else:
            self._stall_count = 0
            self._last = key
        return self._stall_count >= self.stall_threshold

    def state(self) -> dict:
        return {"stall_count": self._stall_count,
                "last": self._last, "threshold": self.stall_threshold}
