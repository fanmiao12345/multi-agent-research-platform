# -*- coding: utf-8 -*-
"""
orchestration/event_bus.py —— Agent 间通信的事件总线（EventBus）

设计动机：编排策略（pipeline / fanout / manager_worker ...）不该直接调用
"记录日志 / 更新 UI / 写 Trace" 的具体实现——那是通信的耦合。策略只向总线
publish 事件，谁关心谁 subscribe（Trace 桥、进度条、评测采集器都是普通订阅者）。

零依赖实现要点：
- 线程安全：fanout/debate 在线程池里发事件，用锁保护订阅者表与序号。
- 有序：单调递增 seq；同一线程内 publish 的顺序即投递顺序。
- 不丢历史：有界 deque 保存最近事件，供事后对账（replay）。
- 订阅者异常绝不反噬发布方：单个订阅者抛错只记入 errors，继续投递其余。

demo：python -m src.orchestration.event_bus
"""

from __future__ import annotations

import datetime
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

# 常用事件类型（收敛命名，避免各处手写字符串）
EV_STAGE_START = "stage_start"          # 一个编排阶段开始（某角色开始干活）
EV_STAGE_END = "stage_end"              # 阶段结束（带 ok / 耗时）
EV_HANDOFF = "handoff"                  # 上一棒产物交接给下一棒
EV_SUBTASK_DISPATCH = "subtask_dispatch"  # fanout / dynamic_team 派发子任务
EV_SUBTASK_DONE = "subtask_done"        # 子任务完成（成功或失败）
EV_WORKER_START = "worker_start"        # worker 真正开始一次 Agent 调用
EV_WORKER_END = "worker_end"            # worker 调用结束
EV_DELEGATE = "delegate"                # manager_worker / 子智能体派工
EV_JUDGE = "judge"                      # debate 裁决
EvHandler = Callable[[dict], Any]


class EventBus:
    """进程内同步事件总线：publish / subscribe / unsubscribe / replay。"""

    def __init__(self, *, history_size: int = 500):
        self._lock = threading.Lock()
        self._handlers: list[tuple[EvHandler, frozenset | None]] = []
        self._history: deque[dict] = deque(maxlen=history_size)
        self._seq = 0
        self.handler_errors: list[str] = []

    # ---- 发布 ----
    def publish(self, event_type: str, source: str = "", **payload) -> dict:
        """发一个事件：所有匹配订阅者按订阅顺序同步收到；返回投递回执。"""
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "type": event_type,
                "source": source,
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
            }
            event.update(payload)
            self._history.append(event)
            handlers = list(self._handlers)
        for handler, only_types in handlers:
            if only_types is not None and event_type not in only_types:
                continue
            try:
                handler(event)
            except Exception as e:  # noqa: BLE001 —— 订阅者异常不反噬发布方
                self.handler_errors.append(f"{getattr(handler, '__name__', '?')}: {e}")
        return event

    # ---- 订阅 ----
    def subscribe(self, handler: EvHandler, *, only: tuple[str, ...] | None = None) -> EvHandler:
        """注册订阅者；only 限定只收这些事件类型（None = 全收）。返回 handler 便于装饰器。"""
        with self._lock:
            self._handlers.append((handler, frozenset(only) if only else None))
        return handler

    def unsubscribe(self, handler: EvHandler) -> bool:
        # 绑定方法每次访问都是新对象，不能用 is 身份比较，用 == 值比较
        with self._lock:
            before = len(self._handlers)
            self._handlers = [(h, t) for h, t in self._handlers if h != handler]
            return len(self._handlers) < before

    # ---- 历史与便捷方法 ----
    def replay(self, event_type: str | None = None) -> list[dict]:
        """回放历史事件（可按类型过滤），供事后对账 / 评测采集。"""
        with self._lock:
            events = list(self._history)
        if event_type is None:
            return events
        return [e for e in events if e["type"] == event_type]

    def clear(self) -> None:
        with self._lock:
            self._history.clear()
            self.handler_errors.clear()


def tracer_bridge(tracer, agent: str = "orchestrator") -> EvHandler:
    """把总线事件桥接进 Tracer（trace.jsonl）——解耦的收益：策略不认识 Tracer，
    装配方认识。返回的 handler 可直接 subscribe 到任意 EventBus。"""

    def _on_event(event: dict) -> None:
        tracer.event(event.get("type", "bus_event"), node="orchestration", agent=agent,
                     bus_seq=event.get("seq"), bus_source=event.get("source", ""),
                     **{k: v for k, v in event.items()
                        if k not in ("seq", "type", "source", "timestamp")})

    return _on_event


def main() -> None:  # pragma: no cover —— 演示用
    bus = EventBus()
    bus.subscribe(lambda ev: print(f"[进度条] {ev['type']} {ev.get('role', '')}"),
                  only=(EV_STAGE_START, EV_STAGE_END))
    bus.publish(EV_STAGE_START, source="pipeline", role="researcher", stage=1)
    bus.publish(EV_STAGE_END, source="pipeline", role="researcher", stage=1, ok=True)
    print(f"历史事件 {len(bus.replay())} 条")


if __name__ == "__main__":
    main()
