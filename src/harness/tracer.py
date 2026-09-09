# -*- coding: utf-8 -*-
"""
harness/tracer.py —— 本地 Trace（DEV_PLAN B1 / 步骤 28）

统一事件字段（B1 Schema 的 M1 版）：
run_id / thread_id / event_id / parent_event_id / node / agent / task_id / model /
timestamp / latency / input_tokens / output_tokens / tool_calls / state_delta /
status / error / type

输出：workspaces/<run_id>/trace.jsonl（每行一个事件，JSON）。
Trace 是第一手调试资料：必须能回答"发生了什么、什么时候、花了多少、错在哪"。
"""

from __future__ import annotations

import datetime
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# 事件类型常量（收敛命名，避免各处手写字符串漂移）
EV_RUN_START = "run_start"
EV_RUN_END = "run_end"
EV_LLM_CALL = "llm_call"
EV_TOOL_CALL = "tool_call"
EV_TOOL_RESULT = "tool_result"
EV_STATE_UPDATE = "state_update"
EV_LOOP_GUARD = "loop_guard"
EV_NODE_START = "node_start"
EV_NODE_END = "node_end"
EV_FINAL = "final"

TRACE_FIELD_SCHEMA = (
    "run_id", "thread_id", "event_id", "parent_event_id", "node", "agent",
    "task_id", "model", "timestamp", "latency", "input_tokens", "output_tokens",
    "tool_calls", "state_delta", "status", "error", "type",
)


class Tracer:
    """一次 Run 一个实例；thread-safe 足够用于单线程/顺序执行。"""

    def __init__(self, run_dir: str | Path, *, run_id: str = "",
                 thread_id: str = "", model: str = "", agent: str = ""):
        self.path = Path(run_dir) / "trace.jsonl"
        self.run_id = run_id
        self.thread_id = thread_id or run_id
        self.model = model
        self.agent = agent

    def event(self, event_type: str, node: str = "", *,
              agent: str = "", task_id: str = "", parent_event_id: str | None = None,
              model: str | None = None, latency: float | None = None,
              input_tokens: int | None = None, output_tokens: int | None = None,
              tool_calls=None, state_delta=None, status: str | None = None,
              error: str | None = None, **extra) -> dict:
        """写一个事件；**extra 允许追加自定义字段（不破坏 Schema 的向后兼容）。"""
        ev = {
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "event_id": uuid.uuid4().hex[:10],
            "parent_event_id": parent_event_id,
            "node": node,
            "agent": agent or self.agent,
            "task_id": task_id,
            "model": model if model is not None else self.model,
            # Windows 的 C strftime 不支持 %f，datetime.strftime 的 %f 由 Python 实现，安全
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
            "latency": round(latency, 4) if latency is not None else None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tool_calls": tool_calls,
            "state_delta": state_delta,
            "status": status,
            "error": error,
            "type": event_type,
        }
        ev.update(extra)
        self._write(ev)
        return ev

    @contextmanager
    def timed_event(self, event_type: str, node: str = "", **fields) -> Iterator[dict]:
        """带耗时的写事件：进入计时，退出时补 latency 并落盘。"""
        started = time.perf_counter()
        ev: dict = {}
        try:
            yield ev
        finally:
            ev["latency"] = round(time.perf_counter() - started, 4)
            self.event(event_type, node, **{**fields, **ev})

    def _write(self, ev: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
