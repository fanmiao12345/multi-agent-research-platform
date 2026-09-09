# -*- coding: utf-8 -*-
"""
workspace.py —— 共享工作区（Blackboard-lite）

一次多智能体运行 = 一个时间戳目录。目录里除了各棒产物的 .md 文件，
还有一份 state.json 当作「黑板」：记录任务、规格、每一步（角色/文件名/
验收结论/反馈/时间戳）—— 任何一棒或主控都能读它了解"现在进行到哪、谁干了什么"。
为将来 Fan-out 并行合并、断点续跑留好了地基。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESEARCH_OUT = os.path.join(BASE_DIR, "research_output")


def _write_text(path: str, text: str) -> None:
    """带轻量重试的写文件（本机沙箱/杀软偶发 PermissionError，重试可自愈）。"""
    for attempt in range(4):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.05)


def new_run_dir(topic: str, engine: str = "pipeline") -> str:
    """建一个本次运行的独立工作区目录：research_output/<时间戳_主题>/"""
    slug = re.sub(r'[\\/:*?"<>|\s]+', "_", (topic or "untitled").strip())[:24] or "untitled"
    d = os.path.join(RESEARCH_OUT,
                     datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + slug)
    os.makedirs(d, exist_ok=True)
    return d


def _state_path(workspace: str) -> str:
    return os.path.join(workspace, "state.json")


def init_state(workspace: str, task: str, spec: dict | None, engine: str) -> None:
    """在黑板 state.json 里写下任务书。"""
    state = {
        "engine": engine,
        "task": task,
        "spec": spec or {},
        "started": datetime.datetime.now().isoformat(timespec="seconds"),
        "steps": [],          # [{seq, role, name, verdict, feedback, preview, at}]
        "final": None,
    }
    _save_state(workspace, state)


def read_state(workspace: str) -> dict:
    try:
        with open(_state_path(workspace), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(workspace: str, state: dict) -> None:
    _write_text(_state_path(workspace), json.dumps(state, ensure_ascii=False, indent=2))


def record_artifact(workspace: str, name: str, text: str, role: str = "",
                    verdict: str = "", feedback: str = "") -> str:
    """把一棒产物写进工作区：.md 文件 + 黑板记录。返回 md 文件路径。"""
    path = os.path.join(workspace, name)
    _write_text(path, text)
    state = read_state(workspace)
    state.setdefault("steps", []).append({
        "seq": len(state.get("steps", [])) + 1,
        "role": role,
        "name": name,
        "verdict": verdict,
        "feedback": feedback,
        "preview": (text or "")[:200],
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
    })
    _save_state(workspace, state)
    return path


def set_final(workspace: str, name: str, text: str) -> str:
    """标记终稿：写入 final.md 并更新黑板。返回 final.md 路径。"""
    path = os.path.join(workspace, name)
    _write_text(path, text)
    state = read_state(workspace)
    state["final"] = name
    _save_state(workspace, state)
    return path


def archive_session(meta: dict, history: list[dict] | None, topic: str = "",
                    engine: str = "agent") -> str:
    """归档一次单智能体会话/问答（A2）：写 agent_runs/<时间戳_主题>/ 目录。

    meta：{'mode': one-shot|chat, 'llm': ..., 'model': ..., 'key_source': ...,
           'question': ...} 等；history：完整 messages。
    返回档案目录路径。agent.py 的 last_transcript.json 仍保留为"最近一次"速查。
    """
    slug = re.sub(r'[\\/:*?"<>|\s]+', "_", (topic or "session").strip())[:24] or "session"
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(BASE_DIR, "agent_runs", f"{stamp}_{slug}")
    os.makedirs(run_dir, exist_ok=True)

    full = dict(meta)
    full["engine"] = engine
    full["archived_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    full["messages"] = len(history or [])
    _write_text(os.path.join(run_dir, "meta.json"),
                json.dumps(full, ensure_ascii=False, indent=2))
    _write_text(os.path.join(run_dir, "transcript.json"),
                json.dumps(history or [], ensure_ascii=False, indent=2))
    return run_dir
