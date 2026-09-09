# -*- coding: utf-8 -*-
"""
harness/control/idempotency.py —— 副作用幂等（DEV_PLAN H3 / 步骤 85）

有副作用 Tool（write/send/create/delete）在 Resume 后不能被重复执行。
ExecutionLedger 记录 (run_id, tool, 参数哈希) → 结果；再次遇到同一键直接回放。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class ExecutionLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, dict] = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _flush(self) -> None:
        self.path.write_text(json.dumps(self._records, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    @staticmethod
    def _key(run_id: str, tool: str, args: dict) -> str:
        digest = hashlib.sha1(
            json.dumps(args, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return f"{run_id}:{tool}:{digest}"

    def execute_once(self, run_id: str, tool: str, args: dict, func) -> tuple[str, str]:
        """执行一次副作用工具。返回 (结果文本, 模式)：
        mode='executed' 真的执行了；mode='replayed' 幂等回放历史结果。"""
        key = self._key(run_id, tool, args)
        if key in self._records:
            return self._records[key]["result"], "replayed"
        result = str(func(**args))
        self._records[key] = {"tool": tool, "args": args, "result": result}
        self._flush()
        return result, "executed"
