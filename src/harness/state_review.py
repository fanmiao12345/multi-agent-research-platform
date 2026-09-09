# -*- coding: utf-8 -*-
"""
harness/state_review.py —— State Review + Resume（DEV_PLAN H9 / 步骤 96）

让人能查看/编辑运行状态后继续：
- review_run：把 run 目录的 run.json + plan.json 汇总成可读视图
- edit_plan_task：改某个 task 的字段（如 description/status 复位）
- resume_run：编辑后调用 durable.resume_plan 继续
"""

from __future__ import annotations

import json
from pathlib import Path

from src.harness.durable import resume_plan


def review_run(run_dir: str | Path) -> dict:
    run_dir = Path(run_dir)
    view: dict = {"run_dir": str(run_dir)}
    run_json = run_dir / "run.json"
    if run_json.exists():
        view["run"] = json.loads(run_json.read_text(encoding="utf-8"))
    plan_json = run_dir / "plan.json"
    if plan_json.exists():
        view["plan"] = json.loads(plan_json.read_text(encoding="utf-8"))
    return view


def edit_plan_task(run_dir: str | Path, task_id: str, **patch) -> bool:
    """编辑 plan.json 中某个 task（patch 直接覆盖字段）。"""
    run_dir = Path(run_dir)
    plan_json = run_dir / "plan.json"
    if not plan_json.exists():
        return False
    plan = json.loads(plan_json.read_text(encoding="utf-8"))
    for task in plan.get("tasks", []):
        if task["id"] == task_id:
            for k, v in patch.items():
                task[k] = v
            plan_json.write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            return True
    return False


def resume_run(run_dir: str | Path, worker, **kwargs) -> dict:
    return resume_plan(run_dir, worker, **kwargs)
