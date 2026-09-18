# -*- coding: utf-8 -*-
"""EventBus：发布订阅 / 过滤 / 解订 / 线程安全 / 编排接线 / Trace 桥。"""
import threading

import pytest

from src.orchestration.event_bus import (EV_STAGE_END, EV_STAGE_START, EventBus,
                                         tracer_bridge)
from src.orchestration.fanout import run_fanout
from src.orchestration.pipeline import run_pipeline


def test_publish_subscribe_and_filter():
    bus = EventBus()
    seen_all, seen_stage = [], []
    bus.subscribe(seen_all.append)
    bus.subscribe(seen_stage.append, only=(EV_STAGE_START,))

    bus.publish(EV_STAGE_START, source="t", role="researcher")
    bus.publish("other_event", source="t")

    assert len(seen_all) == 2                       # 全收订阅者收到全部
    assert [e["type"] for e in seen_stage] == [EV_STAGE_START]  # 过滤订阅者只收一个
    assert seen_all[0]["seq"] < seen_all[1]["seq"]  # 单调序号
    assert seen_all[0]["source"] == "t"


def test_unsubscribe_and_subscriber_error_isolation():
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)

    def bad(_):
        raise RuntimeError("订阅者炸了")
    bus.subscribe(bad)

    bus.publish("x", source="t")
    assert len(seen) == 1                            # 其余订阅者不受影响
    assert len(bus.handler_errors) == 1              # 异常被记录

    assert bus.unsubscribe(seen.append) is True
    bus.publish("y", source="t")
    assert len(seen) == 1


def test_replay_history():
    bus = EventBus()
    bus.publish("a"), bus.publish("b"), bus.publish("a")
    assert len(bus.replay()) == 3
    assert len(bus.replay("a")) == 2
    bus.clear()
    assert bus.replay() == []


def test_pipeline_publishes_stage_and_handoff_events():
    bus = EventBus()
    worker = lambda task, role: f"{role} 完成"
    result = run_pipeline("写报告", worker, event_bus=bus)
    types = [e["type"] for e in bus.replay()]
    assert types.count("stage_start") == 4           # 四棒各一条
    assert types.count("stage_end") == 4
    assert types.count("handoff") == 3               # 三次交接
    assert result.worker_calls == 4


def test_fanout_events_from_worker_threads():
    bus = EventBus()
    run_fanout("并行任务", lambda t, r: "ok", ["甲", "乙", "丙", "丁"],
               max_parallel=3, event_bus=bus)
    dispatches = bus.replay("subtask_dispatch")
    dones = bus.replay("subtask_done")
    assert len(dispatches) == 4 and len(dones) == 4
    assert all(e["ok"] for e in dones)
    assert any(e["type"] == "fanin" for e in bus.replay())


def test_tracer_bridge_writes_events(tmp_path):
    from src.harness.tracer import Tracer
    tracer = Tracer(tmp_path, run_id="r1")
    bus = EventBus()
    bus.subscribe(tracer_bridge(tracer))
    bus.publish(EV_STAGE_START, source="pipeline", role="writer", stage=2)

    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").strip().splitlines()
    import json
    event = json.loads(lines[0])
    assert event["type"] == EV_STAGE_START
    assert event["node"] == "orchestration"
    assert event["role"] == "writer"


def test_worker_bus_reports_start_end(tmp_path):
    from src.orchestration.base import make_worker

    class FakeOutcome:
        final_text = "好"
        termination_reason = "success"

    class FakeRuntime:
        def run_task(self, task, **kwargs):
            return FakeOutcome()

    bus = EventBus()
    worker = make_worker(FakeRuntime(), event_bus=bus)
    out = worker("任务", "agent")
    assert out == "好"
    types = [e["type"] for e in bus.replay()]
    assert types == ["worker_start", "worker_end"]
