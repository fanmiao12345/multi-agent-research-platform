# -*- coding: utf-8 -*-
"""
harness/durable.py —— Durable Execution（DEV_PLAN H1/H2 / 步骤 84、96）

跨进程断点续跑：
- save_plan / load_plan：把 Plan 状态持久化到 run 目录 plan.json
- resume_plan：加载上次状态，已完成任务直接跳过（幂等），只执行未完成的；
  每个任务完成后立即落盘 —— "进程崩溃"也不会白跑。

H2 验收场景：Worker1 ✅ → Worker2 执行中进程退出 → 重启 → 从 Worker2 继续。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.harness.planning.task import COMPLETED, PENDING, RUNNING, FAILED, Plan
from src.harness.planning.task_graph import TaskGraph
from src.harness.run_store import write_json


def save_plan(run_dir: str | Path, plan: Plan) -> Path:
    path = Path(run_dir) / "plan.json"
    write_json(path, plan.to_dict())
    return path


def load_plan(run_dir: str | Path) -> Plan | None:
    path = Path(run_dir) / "plan.json"
    if not path.exists():
        return None
    try:
        return Plan.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def resume_plan(run_dir: str | Path, worker, *, run_timeout: float | None = None,
                fail_fast: bool = True, retry_interrupted: bool = False) -> dict:
    """单个调用方恢复计划；确认中断任务可重放后传 retry_interrupted=True。

    RUNNING 无法判断外部副作用是否已发生，默认报错而不是静默停住或重复执行。
    """
    run_dir = Path(run_dir)
    plan = load_plan(run_dir)
    if plan is None:
        raise FileNotFoundError(f"{run_dir} 没有 plan.json，无法恢复")
    interrupted = [t for t in plan.tasks if t.status == RUNNING]
    if interrupted and not retry_interrupted:
        raise RuntimeError("发现中断任务：" + ", ".join(t.id for t in interrupted)
                           + "；确认原进程已退出且任务可安全重跑后，传 retry_interrupted=True")
    for task in interrupted:
        task.reset()
    if interrupted:
        save_plan(run_dir, plan)
    started = time.perf_counter()
    executed_new: list[str] = []
    skipped = [t.id for t in plan.tasks if t.status == COMPLETED]

    guard = 0
    graph = TaskGraph(plan)
    while not graph.all_completed() and guard < len(plan.tasks) * 6 + 6:
        guard += 1
        if run_timeout is not None and (time.perf_counter() - started) >= run_timeout:
            break
        ready = graph.ready_tasks()
        if not ready:
            # 没有就绪任务但仍有 FAILED 且未重试过 → 自动复位一次再跑
            retried = False
            for task in plan.tasks:
                if task.status == FAILED and task.attempts < 1:
                    task.reset()
                    retried = True
            if retried:
                save_plan(run_dir, plan)
                continue
            break
        for task in ready:
            task.status = "RUNNING"
            save_plan(run_dir, plan)           # 崩溃前先把"正在跑"落盘
            try:
                task.result = worker(task)
                task.status = COMPLETED
            except Exception as e:             # noqa: BLE001
                task.status = FAILED
                task.error = str(e)
                if fail_fast:
                    save_plan(run_dir, plan)
                    raise
            executed_new.append(task.id)
            save_plan(run_dir, plan)           # 每完成一步立即持久化

    return {"run_dir": str(run_dir), "resumed_from_skipped": skipped,
            "executed_new": executed_new,
            "completed_total": sum(1 for t in plan.tasks if t.status == COMPLETED),
            "all_completed": graph.all_completed(),
            "statuses": {t.id: t.status for t in plan.tasks}}
