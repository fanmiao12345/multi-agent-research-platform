# -*- coding: utf-8 -*-
"""
interfaces/web/workbench.py —— Web Agent Workbench（DEV_PLAN K1-K9 / 111-119）

纯标准库（http.server）实现，把新 Harness 的运行产物（workspaces/run.json +
trace.jsonl + usage.json + plan.json）暴露成 9 个面板的 API，并托管一页最小
前端（Dashboard/Timeline/Streaming/Tool Cards/Plan/Workspace/Trace/HITL/Eval）。

启动：python -m src.interfaces.web.workbench [--port 8765]
页面：http://127.0.0.1:8765/
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from config.settings import PROJECT_ROOT
from src.harness.models.factory import ModelConfigError, diagnose_config
from src.harness.run_store import write_json
from src.harness.storage.paths import resolve_under

DEFAULT_WORKSPACES = PROJECT_ROOT / "workspaces"
EVAL_REPORT = PROJECT_ROOT / "eval" / "reports" / "benchmark_report.json"
REAL_EVAL_REPORT = PROJECT_ROOT / "eval" / "reports" / "benchmark_real_report.json"

_JOB_ID = re.compile(r"^job_[0-9a-f]{32}$")
_ITEM_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_WORKER_OWNER = "web-worker"
_MAX_POST_BYTES = 1024 * 1024   # S5-09：请求体上限
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _load(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _revision_payload(workspaces: Path, job_id: str, instruction: str) -> dict:
    """构造改稿队列负载：原稿=最新 report 产物全文，资料=原任务来源全文。"""
    from src.harness.storage.sources import SourceStore
    from src.harness.storage.artifacts import ArtifactStore
    job_dir = workspaces / "jobs" / job_id
    if not (job_dir / "sources.json").exists():
        raise ValueError("任务不存在或没有资料，无法追问改稿")
    store = SourceStore(job_dir)
    texts = []
    for source in store.summary()["sources"]:
        if source.get("status") not in ("ok", "partial") or not source.get("file_name"):
            continue
        text = store.full_text(source["source_id"])
        if text:
            texts.append(text)
    if not texts:
        raise ValueError("原任务没有可用来源全文，无法追问改稿")
    try:
        reports = [a for a in ArtifactStore(job_dir).list() if a.get("kind") == "report"]
        if not reports:
            raise ValueError("原任务没有报告产物，无法作为改稿原稿")
        latest = sorted(reports, key=lambda a: a.get("version") or 0)[-1]
        base_draft = ArtifactStore(job_dir).read(latest["artifact_id"]).get("text", "")
    except Exception as e:
        raise ValueError(f"读取原任务报告失败：{type(e).__name__}") from e
    snapshot = {}
    request_path = job_dir / "request.json"
    if request_path.exists():
        try:
            snapshot = json.loads(request_path.read_text(encoding="utf-8"))
        except Exception:
            snapshot = {}
    return {"task": instruction, "mode": snapshot.get("mode") or "mock",
            "flow": "research", "texts": texts, "base_draft": base_draft,
            "revises_job": job_id,
            "max_calls": int(snapshot.get("max_calls") or 12),
            "max_output_tokens": int(snapshot.get("max_output_tokens") or 8192),
            "max_seconds": float(snapshot.get("max_seconds") or 300)}


def _host_ok(host_header: str) -> bool:
    """S5-09：写接口只接受本机回环 Host。"""
    host = (host_header or "").strip().lower()
    hostname = host.split(":", 1)[0].strip("[]") if ":" in host else host
    return hostname in _ALLOWED_HOSTS


class WorkbenchState:
    """跨请求共享：workspaces 根 + 启动中的 run 线程表 + S4 队列（S5 研究任务）。"""

    def __init__(self, workspaces: Path, tool_registry=None, approval_timeout: float = 300,
                 settings=None, state_db=None):
        self.workspaces = workspaces
        self.running: dict[str, threading.Thread] = {}
        self.pending: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.tool_registry = tool_registry
        self.approval_timeout = approval_timeout
        self.settings = settings
        # S4/S5：状态库与队列（研究任务经队列执行，agent 任务保持原直跑）
        from src.harness.state.db import StateDb
        from src.harness.state.queue import JobQueue
        if state_db is None:
            workspaces.mkdir(parents=True, exist_ok=True)
            state_db = StateDb(workspaces / "state.sqlite")
        self.state_db = state_db
        self.queue = JobQueue(state_db)
        from src.harness.state.pending_inputs import PendingInputStore
        self.pending_inputs = PendingInputStore(state_db)
        self._worker_stop = threading.Event()
        self._worker_thread: threading.Thread | None = None

    def _ensure_worker(self):
        with self.lock:
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._worker_stop.clear()
                self._worker_thread = threading.Thread(
                    target=self._worker_loop, name="s5-worker", daemon=True)
                self._worker_thread.start()

    def stop_worker(self):
        self._worker_stop.set()

    # ---- S5 研究任务执行器（S4-03 接线：submit→claim→run→release） ----------
    def _worker_loop(self):
        while not self._worker_stop.is_set():
            try:
                claims = self.queue.claim(_WORKER_OWNER, lease_seconds=600, limit=1)
            except Exception:
                claims = []
            if not claims:
                time.sleep(0.25)
                continue
            claim = claims[0]
            try:
                if claim.cancel_requested:
                    self.queue.release(_WORKER_OWNER, claim.job_id, to="cancelled",
                                       message="任务在启动前已被取消")
                    continue
                self._execute_claim(claim)
            except Exception as e:  # noqa: BLE001 —— 执行失败要收尾而不是卡住
                try:
                    self.queue.release(_WORKER_OWNER, claim.job_id, to="failed",
                                       error=type(e).__name__,
                                       message=str(e)[:300])
                except Exception:
                    pass

    def _execute_claim(self, claim) -> None:
        from src.application.request import TaskRequest
        from src.application.research import ResearchApplication
        from src.harness.models import factory
        payload = json.loads(claim.request_json or "{}")
        request = TaskRequest.from_payload(payload)
        if request.flow != "research":
            self.queue.release(_WORKER_OWNER, claim.job_id, to="failed",
                               message="队列只接受 research 流程")
            return
        llm = factory.build_adapter(request.profile, self.settings, mode=request.mode)
        if getattr(llm, "run_mode", None) != request.mode:
            self.queue.release(_WORKER_OWNER, claim.job_id, to="failed",
                               message="模型配置与请求模式不一致")
            return

        def should_stop():
            row = self.queue.get(claim.job_id) or {}
            return bool(row.get("cancel_requested"))

        def stage_hook(stage, status, artifact_ids, message):
            try:
                with self.state_db.write_tx() as conn:
                    conn.execute("UPDATE jobs SET stage=? WHERE job_id=?",
                                 (f"{stage}:{status}", claim.job_id))
            except Exception:  # noqa: BLE001
                pass

        app = ResearchApplication(request, settings=self.settings,
                                  workspace_root=self.workspaces, llm=llm)
        result = app.run(job_id=claim.job_id, stage_hook=stage_hook,
                         should_stop=should_stop)
        mapping = {"success": "completed", "incomplete": "partial",
                   "budget_exceeded": "cancelled", "cancelled": "cancelled",
                   "error": "failed"}
        target = mapping.get(getattr(result, "termination_reason", ""), "failed")
        self.queue.release(_WORKER_OWNER, claim.job_id, to=target,
                           stage="finished",
                           message=getattr(result, "message", "")[:300])


    def list_runs(self) -> list[dict]:
        out = []
        if self.workspaces.is_dir():
            for d in sorted(self.workspaces.iterdir(), reverse=True):
                run_json = d / "run.json"
                if run_json.exists():
                    meta = _load(run_json) or {}
                    meta["dir"] = str(d)
                    meta["trace_count"] = WorkbenchState._line_count(d / "trace.jsonl")
                    out.append(meta)
        return out

    def start_run(self, payload: dict) -> dict:
        from src.application.request import TaskRequest
        from src.harness.planning.understanding import (persist_input_request,
                                                        understand_task)
        request = TaskRequest.from_payload(payload)
        plan_only = bool(payload.get("plan_only"))
        if request.flow == "research":
            understanding = understand_task(
                request, network_available=bool(getattr(self.settings, "search_provider", "")))
            if understanding.needs_input:
                job_id = self.queue.submit(kind="research", request=payload,
                                           stage="waiting_input")
                job_dir = self.workspaces / "jobs" / job_id
                persist_input_request(job_dir, understanding)
                self.pending_inputs.create(
                    job_id=job_id, questions=understanding.questions,
                    target=request.task, params=payload,
                    budget={"max_calls": request.max_calls,
                            "max_cost": request.max_cost,
                            "max_seconds": request.max_seconds},
                    plan_version=1)
                # D8-04：挂起为 waiting_input，否则已在常驻的 worker 会抢先执行缺条件的任务
                self.queue.hold_for_input(job_id)
                self.queue.update_progress(job_id, stage="waiting_input",
                                           message="waiting_input")
                return {"status": "waiting_input", "job_id": job_id,
                        "task": request.task, "understanding": understanding.to_dict()}
            if plan_only:
                from src.application.orchestration import (OrchestrationScheduler,
                                                           caps_of)
                plan, meta = OrchestrationScheduler(None).plan(
                    request.task, required_sections=request.required_sections,
                    budget_caps=caps_of(request), understanding=understanding)
                return {"status": "planned", "plan": plan.as_dict(), "meta": meta,
                        "task": request.task}
            job_id = self.queue.submit(kind="research", request=payload,
                                       stage="queued")
            self._ensure_worker()
            return {"status": "queued", "job_id": job_id, "task": request.task}
        from src.application.research import ResearchApplication
        from src.harness.tools.executor import ToolExecutor
        from src.harness.tools.registry import ToolRegistry
        task = request.task
        registry = self.tool_registry or ToolRegistry.with_builtins()
        application = ResearchApplication(request, settings=self.settings,
            workspace_root=self.workspaces, tool_executor=ToolExecutor(registry))
        holder: dict = {}
        started = threading.Event()

        def on_event(event):
            if event["type"] == "run_start":
                holder["run_id"] = event["run_id"]
                with self.lock:
                    self.running[event["run_id"]] = threading.current_thread()
                started.set()

        def worker():
            try:
                application.run(on_event=on_event,
                                 approval_handler=lambda spec, args: self.wait_for_approval(
                                     holder["run_id"], spec, args))
            except Exception:
                pass  # Runtime 已持久化失败终态；未创建 run 的启动失败由下面的检查返回。
            finally:
                with self.lock:
                    self.running.pop(holder.get("run_id"), None)
                started.set()

        # 从本次 Runtime 的回调取得准确 id，避免把并发请求关联到“最新目录”。
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        if not started.wait(10) or "run_id" not in holder:
            raise RuntimeError("任务启动失败，请检查本地运行目录及配置")
        return {"status": "ok", "task": task, "run_id": holder["run_id"]}

    def wait_for_approval(self, run_id: str, spec, arguments: dict) -> bool:
        request = {"request_id": uuid.uuid4().hex, "name": spec.name,
                   "arguments": arguments, "action": None, "event": threading.Event()}
        with self.lock:
            self.pending[run_id] = request
        try:
            request["event"].wait(self.approval_timeout)
            with self.lock:
                self.pending.pop(run_id, None)
                return request["action"] == "approve"
        finally:
            with self.lock:
                self.pending.pop(run_id, None)

    def approval_view(self, run_id: str) -> dict:
        with self.lock:
            request = self.pending.get(run_id)
            if request is None or request["action"] is not None:
                return {"pending": None}
            return {"pending": {k: request[k] for k in ("request_id", "name", "arguments")}}

    def decide(self, run_id: str, request_id: str, action: str) -> None:
        if action not in ("approve", "reject"):
            raise ValueError("action 必须为 approve 或 reject")
        with self.lock:
            request = self.pending.get(run_id)
            if request is None or request["request_id"] != request_id or request["action"] is not None:
                raise LookupError("没有匹配的待审批调用，决策可能已提交或已过期")
            path = self.workspaces / run_id / "hitl_decision.json"
            write_json(path, {"request_id": request_id, "action": action, "name": request["name"]})
            request["action"] = action
            request["event"].set()

    @staticmethod
    def _line_count(path: Path) -> int:
        try:
            return sum(1 for _ in path.open(encoding="utf-8"))
        except Exception:
            return 0


class Handler(BaseHTTPRequestHandler):
    state: WorkbenchState = WorkbenchState(DEFAULT_WORKSPACES)

    # ---- helpers ----
    def _send(self, code: int, payload, content_type="application/json; charset=utf-8",
              extra_headers: dict | None = None):
        if isinstance(payload, bytes):
            body = payload
        elif content_type.startswith("text/"):
            # 文本类响应按原文返回（报告/来源全文），不能被 json.dumps 转义成带引号的字面量
            body = payload.encode("utf-8")
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _run_dir(self, run_id: str) -> Path | None:
        if not self.state.workspaces.is_dir():
            return None
        for d in self.state.workspaces.iterdir():
            if d.name == run_id and (d / "run.json").exists():
                return d
        return None

    def _job_dir(self, job_id: str) -> Path | None:
        """job_id 白名单（B2 账本目录格式），避免把任意路径当作任务目录。"""
        if not _JOB_ID.fullmatch(job_id or ""):
            return None
        directory = self.state.workspaces / "jobs" / job_id
        return directory if directory.is_dir() else None

    def _job_task(self, job_id: str) -> str:
        """任务列表用的一句话目标：只读取队列里的 task 字段，不返回粘贴正文或密钥。"""
        if not _JOB_ID.fullmatch(job_id or ""):
            return ""
        row = self.state.queue.get(job_id) or {}
        try:
            payload = json.loads(row.get("request_json") or "{}")
        except Exception:  # noqa: BLE001 —— 快照损坏时列表仍要可用
            payload = {}
        task = payload.get("task") if isinstance(payload, dict) else ""
        return (task or "").strip()[:120]

    def _job_progress(self, job_id: str):
        """任务进度：队列行 + 任务目录产物。刚入队（目录未建）时也返回 200，页面按“排队中”展示。"""
        if not _JOB_ID.fullmatch(job_id or ""):
            return self._send(404, {"error": "job 不存在或 id 格式无效"})
        row = self.state.queue.get(job_id)
        directory = self._job_dir(job_id)
        if row is None and directory is None:
            return self._send(404, {"error": "job 不存在"})
        loaded = (lambda name: _load(directory / name)) if directory is not None else (lambda name: None)
        return self._send(200, {"job": row, "job_file": loaded("job.json"),
                                "pipeline": loaded("pipeline.json"),
                                "ledger": loaded("ledger.json"),
                                "pending_inputs": self.state.pending_inputs.list(job_id),
                                "note": None if directory is not None
                                        else "任务尚未开始执行（等待执行器创建任务目录）"})

    def _resume_job(self, job_id: str):
        """S5-02 恢复：对存在阶段产物/检查点的研究任务排队式恢复（后台线程）。"""
        if not _JOB_ID.fullmatch(job_id or ""):
            return self._send(404, {"error": "job 不存在或 id 格式无效"})
        directory = self.state.workspaces / "jobs" / job_id
        if not directory.is_dir():
            return self._send(404, {"error": "job 不存在"})
        job_file = _load(directory / "job.json") or {}
        if (job_file.get("pipeline") or {}).get("draft_level") == "accepted":
            return self._send(409, {"error": "任务已完成验收，无需恢复"})
        if not (directory / "stage_draft.json").exists() \
                and not (directory / "evidence.json").exists():
            return self._send(409, {"error": "没有可恢复的阶段产物；请重新提交任务"})
        with self.state.lock:
            if "resume:" + job_id in self.state.running:
                return self._send(409, {"error": "该任务正在恢复中"})
            worker = threading.Thread(
                target=self._resume_worker, args=(job_id,),
                name="resume-" + job_id, daemon=True)
            self.state.running["resume:" + job_id] = worker
            worker.start()
        return self._send(202, {"status": "resuming", "job_id": job_id})

    def _revise_job(self, job_id: str, instruction: str):
        """S5-04 追问改稿：入队新任务（原稿=最新报告，资料=同批来源）。"""
        if not _JOB_ID.fullmatch(job_id or ""):
            return self._send(404, {"error": "job 不存在或 id 格式无效"})
        try:
            payload = _revision_payload(self.state.workspaces, job_id, instruction)
        except ValueError as e:
            return self._send(409, {"error": str(e)})
        new_job_id = self.state.queue.submit(kind="research", request=payload,
                                             stage="queued")
        self.state._ensure_worker()
        return self._send(202, {"status": "queued", "job_id": new_job_id,
                                "revises_job": job_id})

    def _resume_worker(self, job_id: str) -> None:
        from src.application.research import resume_research_job
        try:
            resume_research_job(workspace_root=self.state.workspaces, job_id=job_id,
                                settings=self.state.settings)
        except Exception:  # noqa: BLE001 —— 失败落在 job.json/pipeline 里可查
            pass
        finally:
            with self.state.lock:
                self.state.running.pop("resume:" + job_id, None)

    def _job_api(self, segments: list[str]):
        """/api/jobs/<job_id>/... ：资料与完整产物只读视图（B3）。

        sources 列表 / sources/<sid>/text 全文 / artifacts 列表 /
        artifacts/<aid>/content 内容。全文文件路径经 resolve_under 边界校验。
        """
        # progress 先处理：刚入队的任务还没有目录，但队列行已存在（按"排队中"展示，不报 404）
        if len(segments) == 2 and segments[1] == "progress":
            return self._job_progress(segments[0])
        job_dir = self._job_dir(segments[0])
        if job_dir is None:
            return self._send(404, {"error": "job 不存在"})
        if len(segments) == 2 and segments[1] == "sources":
            index = _load(job_dir / "sources.json") or {}
            records = index.get("sources", []) if isinstance(index, dict) else []
            counts: dict[str, int] = {}
            usable = 0
            for record in records:
                counts[record.get("status", "?")] = counts.get(record.get("status", "?"), 0) + 1
                if record.get("status") in ("ok", "partial"):
                    usable += 1
            return self._send(200, {"sources": records, "total": len(records),
                                    "usable": usable, "statuses": counts})
        if len(segments) == 4 and segments[1] == "sources" and segments[3] == "text":
            source_id = segments[2]
            if not _ITEM_ID.fullmatch(source_id):
                return self._send(404, {"error": "来源 id 格式无效"})
            index = _load(job_dir / "sources.json") or {}
            for record in (index.get("sources", []) if isinstance(index, dict) else []):
                if record.get("source_id") != source_id:
                    continue
                file_name = record.get("file_name")
                if not file_name:
                    return self._send(404, {"error": "该来源没有全文（重复/失败来源）"})
                try:
                    path = resolve_under(job_dir, file_name)
                    text = path.read_text(encoding="utf-8")
                except Exception:
                    return self._send(404, {"error": "来源全文不可读或路径不受控"})
                return self._send(200, text, "text/plain; charset=utf-8")
            return self._send(404, {"error": "来源不存在"})
        if len(segments) == 2 and segments[1] == "artifacts":
            index = _load(job_dir / "artifacts.json") or {}
            artifacts = index.get("artifacts", []) if isinstance(index, dict) else []
            return self._send(200, {"artifacts": artifacts})
        if len(segments) == 2 and segments[1] == "evidence":
            index = _load(job_dir / "evidence.json") or {}
            items = index.get("items", []) if isinstance(index, dict) else []
            return self._send(200, {"evidence": items})
        if len(segments) == 2 and segments[1] == "export.html":
            import html
            index = _load(job_dir / "artifacts.json") or {}
            reports = [a for a in index.get("artifacts", [])
                       if a.get("kind") in ("report", "analysis", "collection")]
            if not reports:
                return self._send(404, {"error": "没有可导出的交付产物"})
            latest = sorted(reports, key=lambda a: a.get("version") or 0)[-1]
            try:
                text = resolve_under(job_dir, latest["file_name"]).read_text(encoding="utf-8")
            except Exception:
                return self._send(404, {"error": "交付产物不可读"})
            page = "<!doctype html><meta charset='utf-8'><title>Report</title><pre>" + html.escape(text) + "</pre>"
            return self._send(200, page, "text/html; charset=utf-8",
                              {"Content-Disposition": "attachment; filename=report.html"})
        if len(segments) == 2 and segments[1] == "process":
            return self._send(200, _load(job_dir / "orchestration.json") or
                              _load(job_dir / "job.json") or {"note": "无过程记录"})
        if len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "content":
            artifact_id = segments[2]
            if not _ITEM_ID.fullmatch(artifact_id):
                return self._send(404, {"error": "产物 id 格式无效"})
            index = _load(job_dir / "artifacts.json") or {}
            for record in (index.get("artifacts", []) if isinstance(index, dict) else []):
                if record.get("artifact_id") != artifact_id:
                    continue
                file_name = record.get("file_name")
                if not file_name:
                    return self._send(404, {"error": "产物记录缺少文件"})
                try:
                    path = resolve_under(job_dir, file_name)
                    text = path.read_text(encoding="utf-8")
                except Exception:
                    return self._send(404, {"error": "产物文件不可读或路径不受控"})
                return self._send(200, text, "text/plain; charset=utf-8")
            return self._send(404, {"error": "产物不存在"})
        if len(segments) == 4 and segments[1] == "artifacts" and segments[3] == "download":
            artifact_id = segments[2]
            if not _ITEM_ID.fullmatch(artifact_id):
                return self._send(404, {"error": "产物 id 格式无效"})
            index = _load(job_dir / "artifacts.json") or {}
            for record in (index.get("artifacts", []) if isinstance(index, dict) else []):
                if record.get("artifact_id") != artifact_id:
                    continue
                file_name = record.get("file_name")
                if not file_name:
                    return self._send(404, {"error": "产物记录缺少文件"})
                try:
                    path = resolve_under(job_dir, file_name)
                    raw = path.read_bytes()
                except Exception:
                    return self._send(404, {"error": "产物文件不可读或路径不受控"})
                # S5-05：只导出任务内登记产物；按附件下发，预览不执行（前端 pre/DOM 渲染）
                return self._send(200, raw, "text/plain; charset=utf-8",
                                  extra_headers={
                                      "Content-Disposition":
                                          f"attachment; filename=\"{record['file_name'].rsplit('/', 1)[-1]}\"",
                                      "X-Content-Type-Options": "nosniff"})
            return self._send(404, {"error": "产物不存在"})
        return self._send(404, {"error": f"未知 job 视图 {self.path}"})

    # ---- GET 页面 ----
    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        if url.path == "/api/runs":
            return self._send(200, {"runs": self.state.list_runs()})
        if url.path == "/api/config":
            query = parse_qs(url.query, keep_blank_values=True)
            info = diagnose_config(
                self.state.settings, mode=query.get("mode", ["mock"])[0],
                profile_name=query.get("profile", [None])[0])
            # 页面只显示"是否配置了联网搜索"这一事实（不含密钥），供用户判断能否只给主题做研究
            info["search_provider"] = (
                getattr(self.state.settings, "search_provider", "") or "").strip()
            return self._send(200, info)
        if url.path == "/api/eval":
            mode = parse_qs(url.query).get("mode", ["mock"])[0]
            if mode not in ("mock", "real"):
                return self._send(400, {"error": "评测模式必须为 mock 或 real"})
            path = REAL_EVAL_REPORT if mode == "real" else EVAL_REPORT
            return self._send(200, _load(path) or {"mode": mode, "note": "尚无该模式的 benchmark 报告"})
        parts = [p for p in url.path.split("/") if p]
        if parts == ["api", "jobs"]:
            jobs = self.state.queue.list(limit=50)
            for job in jobs:
                job["task"] = self._job_task(job.get("job_id", ""))
            return self._send(200, {"jobs": jobs})
        if len(parts) >= 2 and parts[:2] == ["api", "jobs"]:
            return self._job_api(parts[2:])
        if len(parts) >= 4 and parts[:2] == ["api", "runs"]:
            run_id, view = parts[2], parts[3]
            d = self._run_dir(run_id)
            if d is None:
                return self._send(404, {"error": "run 不存在"})
            if view == "trace":
                events = _load_trace(d / "trace.jsonl")
                return self._send(200, {"events": events})
            if view == "events":        # Streaming 轮询：?after=N
                after = int(parse_qs(url.query).get("after", ["0"])[0])
                events = _load_trace(d / "trace.jsonl")
                return self._send(200, {"after": len(events),
                                        "new": events[after:]})
            if view == "tools":
                return self._send(200, {"tools": _tool_cards(d)})
            if view == "plan":
                return self._send(200, _load(d / "plan.json") or {"note": "无 plan"})
            if view == "workspace":
                files = sorted(p.name for p in d.iterdir() if p.is_file())
                return self._send(200, {"files": files})
            if view == "meta":
                return self._send(200, _load(d / "run.json"))
            if view == "job":
                meta = _load(d / "run.json") or {}
                job_id = meta.get("root_job_id", "")
                if not isinstance(job_id, str) or not job_id.startswith("job_") or not job_id[4:].isalnum():
                    return self._send(200, {"note": "历史运行没有根任务账本"})
                directory = self.state.workspaces / "jobs" / job_id
                return self._send(200, {"job": _load(directory / "job.json"),
                                        "ledger": _load(directory / "ledger.json")})
            if view == "hitl":
                return self._send(200, self.state.approval_view(run_id))
        return self._send(404, {"error": f"未知路径 {self.path}"})

    def do_POST(self):  # noqa: N802
        # S5-09：写接口只接受本机回环 Host；限制请求体大小；正文必须 JSON
        if not _host_ok(self.headers.get("Host", "")):
            return self._send(403, {"error": "写接口只接受本机回环 Host（127.0.0.1/localhost）"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "Content-Length 无效"})
        if length > _MAX_POST_BYTES:
            return self._send(413, {"error": f"请求体超过 {_MAX_POST_BYTES} 字节上限"})
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type not in ("application/json", ""):
            return self._send(415, {"error": "写接口只接受 application/json"})
        url = urlparse(self.path)
        if url.path == "/api/runs":
            try:
                info = self.state.start_run(self._body())
                return self._send(200, info)
            except ModelConfigError as e:
                return self._send(400, {"error": str(e)})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"error": f"无法启动任务，请检查输入和配置（{type(e).__name__}）"})
        parts = [p for p in url.path.split("/") if p]
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "input":
            from src.harness.state.queue import HOLD_FOR_INPUT
            body = self._body()
            answer = body.get("answer")
            if not isinstance(answer, dict) or not answer:
                return self._send(400, {"error": "answer 必须为非空对象"})
            pending = self.state.pending_inputs.list(parts[2])
            active = next((item for item in pending if item["status"] == "pending"), None)
            if active is None:
                return self._send(404, {"error": "没有待输入项"})
            row = self.state.queue.get(parts[2]) or {}
            if row.get("status") != HOLD_FOR_INPUT:
                return self._send(409, {"error": "该任务已不在等待补充状态（可能已执行或已结束），"
                                                 "补充内容未合并；如需新条件请新建任务或追问改稿。"})
            self.state.pending_inputs.answer(active["input_id"], answer)
            self.state.queue.merge_request(parts[2], answer)
            self.state.queue.requeue(parts[2], stage="queued", message="input answered")
            self.state._ensure_worker()
            return self._send(200, {"status": "queued", "job_id": parts[2]})
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
            try:
                outcome = self.state.queue.request_cancel(parts[2])
                return self._send(200, outcome)
            except LookupError:
                return self._send(404, {"error": "job 不存在"})
            except ValueError as e:
                return self._send(409, {"error": str(e)})
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "resume":
            return self._resume_job(parts[2])
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "revise":
            body = self._body()
            instruction = (body.get("instruction") or "").strip()
            if not instruction:
                return self._send(400, {"error": "改稿指令不能为空"})
            return self._revise_job(parts[2], instruction)
        if url.path == "/api/hitl/decide":
            body = self._body()
            run_id = body.get("run_id", "")
            d = self._run_dir(run_id) if run_id else None
            if d is None:
                return self._send(404, {"error": "run 不存在或未提供 run_id"})
            try:
                self.state.decide(run_id, body.get("request_id", ""), body.get("action"))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except LookupError as e:
                return self._send(409, {"error": str(e)})
            return self._send(200, {"status": "recorded"})
        return self._send(404, {"error": "未知路径"})

    def log_message(self, *args):  # noqa: D401 —— 安静
        pass


def _load_trace(path: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()
                if x.strip()]
    except Exception:
        return []


def _tool_cards(run_dir: Path) -> list[dict]:
    """从 trace 里把成对的 tool_call/tool_result 做成工具卡。"""
    events = _load_trace(run_dir / "trace.jsonl")
    cards, pending = [], {}
    for ev in events:
        if ev.get("type") == "tool_call":
            key = ev.get("event_id") or ev.get("timestamp")
            pending[key] = {"name": ev.get("name"), "arguments": ev.get("arguments"),
                            "latency": ev.get("latency")}
        elif ev.get("type") == "tool_result":
            name = ev.get("name")
            card = {"name": name, "result": (ev.get("result") or "")[:300],
                    "latency": ev.get("latency")}
            cards.append(card)
    return cards


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Research Console · Multi-Agent Research Platform</title>
<style>
:root{
  --bg:#f5f7fb;--panel:#fff;--panel-soft:#f8fafc;--panel-strong:#111827;
  --line:#e5e7eb;--line-strong:#d0d5dd;--text:#172033;--muted:#667085;--muted2:#98a2b3;
  --accent:#5b5bd6;--accent2:#7676e8;--accent-soft:#efefff;
  --success:#18794e;--success-soft:#e9f8f0;--warn:#b25e09;--warn-soft:#fff4e5;
  --danger:#c4320a;--danger-soft:#fff0ec;--info:#175cd3;--info-soft:#eff8ff;
  --shadow:0 1px 2px rgba(16,24,40,.04),0 8px 24px rgba(16,24,40,.05);
  --shadow-lg:0 18px 50px rgba(16,24,40,.08);
  --mono:"SFMono-Regular",Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 Inter,"Segoe UI","Microsoft YaHei UI","Microsoft YaHei",system-ui,sans-serif}
button,input,select,textarea{font:inherit}
button{cursor:pointer}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
::selection{background:#dedeff;color:#24245f}

.app-shell{min-height:100vh;display:grid;grid-template-columns:244px minmax(0,1fr)}
.sidebar{position:sticky;top:0;height:100vh;padding:20px 14px;border-right:1px solid var(--line);background:rgba(255,255,255,.9);backdrop-filter:blur(14px);z-index:20}
.brand{display:flex;align-items:center;gap:11px;padding:2px 7px 20px}
.brandmark{width:38px;height:38px;border-radius:11px;background:linear-gradient(145deg,#111827,#344054);color:#fff;display:grid;place-items:center;font-weight:800;letter-spacing:-.04em;box-shadow:var(--shadow)}
.brandcopy strong{display:block;font-size:13px;letter-spacing:-.01em}
.brandcopy span{display:block;font-size:10.5px;color:var(--muted2);margin-top:2px}
.navgroup{margin:9px 0 18px}
.navlabel{padding:0 10px 6px;color:var(--muted2);font-size:10px;font-weight:800;letter-spacing:.09em;text-transform:uppercase}
.navitem{display:flex;align-items:center;gap:10px;padding:9px 10px;margin:2px 0;border-radius:9px;color:#475467;font-size:12.5px;font-weight:650;text-decoration:none}
.navitem:hover{background:var(--panel-soft);color:var(--text);text-decoration:none}
.navitem.active{background:var(--accent-soft);color:var(--accent)}
.navitem.active .ico{background:var(--accent);border-color:var(--accent);color:#fff}
section.panel[hidden]{display:none}
.navitem .ico{width:21px;height:21px;border:1px solid var(--line);border-radius:6px;display:grid;place-items:center;font:800 10px var(--mono);background:#fff;color:var(--muted)}
.sidebar-foot{position:absolute;left:14px;right:14px;bottom:16px;padding:11px;border:1px solid var(--line);border-radius:10px;background:var(--panel-soft);color:var(--muted);font-size:10.5px}
.sidebar-foot b{color:var(--text)}
.main{min-width:0;padding:0 26px 54px}
.topbar{max-width:1500px;margin:0 auto;height:70px;display:flex;align-items:center;gap:14px}
.topbar-title{min-width:0}
.topbar-title h1{font-size:18px;line-height:1.2;margin:0;letter-spacing:-.025em}
.topbar-title p{margin:4px 0 0;color:var(--muted);font-size:11.5px}
.topbar-actions{margin-left:auto;display:flex;align-items:center;gap:8px}
.local-pill{display:flex;align-items:center;gap:7px;padding:7px 10px;border:1px solid var(--line);border-radius:999px;background:var(--panel);color:var(--muted);font-size:11px}
.local-dot{width:7px;height:7px;border-radius:50%;background:#22a06b;box-shadow:0 0 0 3px var(--success-soft)}

.content{max-width:1500px;margin:0 auto;display:grid;gap:16px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);min-width:0}
.panel-pad{padding:18px}
.section-head{display:flex;align-items:flex-start;gap:10px;margin-bottom:14px}
.section-num{flex:0 0 auto;width:26px;height:26px;display:grid;place-items:center;border-radius:8px;background:var(--accent-soft);color:var(--accent);font:800 10px var(--mono)}
.section-title{min-width:0}
.section-title strong{display:block;font-size:14px}
.section-title span{display:block;color:var(--muted);font-size:11px;margin-top:2px}
.section-head .right{margin-left:auto}
.divider{height:1px;background:var(--line);margin:14px -18px}

.composer{padding:22px;background:linear-gradient(135deg,#fff 0%,#fbfbff 60%,#f7f7ff 100%);border-color:#ddddf8;box-shadow:var(--shadow-lg)}
.composer-main{display:grid;grid-template-columns:minmax(260px,1fr) 145px 170px auto;gap:9px;align-items:end}
.field{display:flex;flex-direction:column;gap:5px;color:#475467;font-size:11px;font-weight:700}
.field.grow{min-width:0}
input,select,textarea{width:100%;background:#fff;color:var(--text);border:1px solid var(--line-strong);border-radius:9px;padding:9px 10px;transition:border-color .15s,box-shadow .15s}
input:hover,select:hover,textarea:hover{border-color:#b8bec8}
input:focus,select:focus,textarea:focus{outline:0;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
textarea{resize:vertical}
#task{font-size:14px;padding:11px 12px}
.btn{border:1px solid var(--line-strong);background:#fff;color:#344054;border-radius:9px;padding:9px 12px;font-size:12px;font-weight:750;transition:all .15s}
.btn:hover:not(:disabled){transform:translateY(-1px);background:#f9fafb}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn-primary{background:var(--accent);border-color:var(--accent);color:#fff;padding:11px 16px}
.btn-primary:hover:not(:disabled){background:#4d4dc4;border-color:#4d4dc4}
.btn-danger{color:var(--danger);border-color:#f0c5b8}
.btn-quiet{background:transparent}
.inline-actions{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
#config{margin:10px 0 0;color:#475467;font-size:11.5px}
.workflow{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:14px}
.workflow .step{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:10.5px;font-weight:700}
.workflow .step b{width:20px;height:20px;border-radius:50%;display:grid;place-items:center;background:#fff;border:1px solid #d9d9f4;color:var(--accent);font:800 9px var(--mono)}
.workflow .arr{color:#c6c9d0}
.advanced{margin-top:14px;border-top:1px solid #e8e8fa;padding-top:12px}
.advanced summary{cursor:pointer;color:#475467;font-size:11.5px;font-weight:750;user-select:none}
.advanced-grid{display:grid;grid-template-columns:repeat(4,minmax(110px,1fr));gap:9px;margin:11px 0}
.source-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.source-grid .wide{grid-column:1/-1}
.hint{margin:6px 0;color:var(--muted);font-size:10.5px}

.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.metric{padding:13px 14px;background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:0 1px 2px rgba(16,24,40,.03)}
.metric span{display:block;color:var(--muted);font-size:10.5px;font-weight:700}
.metric strong{display:block;margin-top:5px;font-size:19px;letter-spacing:-.03em}
.metric small{display:block;margin-top:2px;color:var(--muted2);font-size:9.5px}

.workspace-grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(360px,.65fr);gap:16px;align-items:start}
.observability-grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(320px,.8fr);gap:16px;align-items:start}
.stack{display:grid;gap:16px}
.split-head{display:flex;align-items:center;gap:8px;justify-content:space-between}
.state-line{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:10px 0}
.mono{font-family:var(--mono)}
.small{font-size:10.5px;color:var(--muted)}
.muted{color:var(--muted)}
.hidden{display:none!important}

.badge{display:inline-flex;align-items:center;gap:5px;border-radius:999px;padding:3px 7px;font-size:9.5px;font-weight:800;white-space:nowrap;background:#f2f4f7;color:#475467}
.badge::before{content:"";width:5px;height:5px;border-radius:50%;background:#98a2b3}
.badge.success{background:var(--success-soft);color:var(--success)}.badge.success::before{background:#22a06b}
.badge.warn{background:var(--warn-soft);color:var(--warn)}.badge.warn::before{background:#f79009}
.badge.danger{background:var(--danger-soft);color:var(--danger)}.badge.danger::before{background:#f04438}
.badge.info{background:var(--info-soft);color:var(--info)}.badge.info::before{background:#2e90fa}
.badge.accent{background:var(--accent-soft);color:var(--accent)}.badge.accent::before{background:var(--accent)}

.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:10px}
table{width:100%;border-collapse:collapse;min-width:580px}
th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;font-size:10.5px}
th{position:sticky;top:0;background:var(--panel-soft);color:var(--muted);font-size:9px;letter-spacing:.05em;text-transform:uppercase;z-index:1}
tr:last-child td{border-bottom:0}
tbody tr:hover td{background:#fbfcfd}
.cell-main{max-width:205px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.empty{padding:28px 16px;text-align:center;color:var(--muted);font-size:11px}
.empty b{display:block;color:#475467;margin-bottom:4px}

.stage-rail{display:grid;gap:7px;margin:12px 0}
.stage{display:grid;grid-template-columns:19px minmax(0,1fr) auto;gap:8px;align-items:start}
.stagedetails{margin:2px 0 12px;border:1px solid var(--line);border-radius:10px;background:var(--panel-soft)}
.stagedetails summary{cursor:pointer;padding:9px 11px;font-size:11.5px;font-weight:750;color:#475467;user-select:none}
.stagedetails[open] summary{border-bottom:1px solid var(--line)}
.stagedetails .stage-rail{padding:2px 11px 9px}
.stage-dot{width:18px;height:18px;border-radius:50%;border:1px solid var(--line-strong);display:grid;place-items:center;font:800 8px var(--mono);color:var(--muted);background:#fff}
.stage.done .stage-dot{background:var(--success-soft);border-color:#abefc6;color:var(--success)}
.stage.running .stage-dot{background:var(--accent-soft);border-color:#c7c7ff;color:var(--accent)}
.stage.fail .stage-dot{background:var(--danger-soft);border-color:#f7c8bb;color:var(--danger)}
.stage-main strong{display:block;font-size:10.5px}.stage-main span{display:block;color:var(--muted);font-size:9.5px;margin-top:1px}
.progressbar{height:6px;border-radius:999px;background:#eef0f3;overflow:hidden}
.progressbar>span{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--accent),#8f8ff3);transition:width .2s}

.report-toolbar{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:10px 0}
.report-reader{border:1px solid var(--line);border-radius:12px;background:#fff;padding:22px;max-height:740px;overflow:auto;box-shadow:inset 0 1px 0 rgba(16,24,40,.02)}
.report-reader h1{font-size:22px;margin:0 0 15px}.report-reader h2{font-size:17px;margin:22px 0 9px}.report-reader h3{font-size:14px;margin:17px 0 7px}
.report-reader p,.report-reader li{font-size:12.5px;line-height:1.8;color:#344054}.report-reader ul{padding-left:20px}
.citebtn{display:inline-flex;align-items:center;border:0;background:var(--accent-soft);color:var(--accent);border-radius:5px;padding:1px 5px;margin:0 2px;font:800 9px var(--mono);vertical-align:baseline}
.citebtn:hover{background:#dedeff}
.evidence-list{display:flex;gap:5px;flex-wrap:wrap;max-height:190px;overflow:auto}
.evidence-list .btn{font-size:9.5px;padding:5px 7px}
.nowrap{white-space:nowrap}
.docview{border:1px solid var(--line);border-radius:10px;background:var(--panel-soft);padding:12px;min-height:92px;max-height:330px;overflow:auto;white-space:pre-wrap;font:10.5px/1.65 var(--mono);color:#475467}

.console{background:#101828;color:#d0d5dd;border:1px solid #1d2939;border-radius:10px;padding:11px 12px;white-space:pre-wrap;overflow:auto;font:10px/1.65 var(--mono)}
#stream{max-height:180px;min-height:72px}
#joblog{max-height:210px}
.trace-list{display:grid;gap:6px;max-height:520px;overflow:auto}
.trace-event{display:grid;grid-template-columns:9px minmax(0,1fr);gap:9px;padding:8px 9px;border:1px solid var(--line);border-radius:9px;background:#fff}
.trace-dot{width:7px;height:7px;border-radius:50%;background:#98a2b3;margin-top:5px}
.trace-event[data-kind*="tool"] .trace-dot{background:#2e90fa}.trace-event[data-kind*="llm"] .trace-dot{background:#7f56d9}.trace-event[data-kind*="run"] .trace-dot{background:#22a06b}
.trace-main strong{font-size:10px}.trace-main span{display:block;color:var(--muted);font-size:9.5px;margin-top:1px}.trace-main code{display:block;color:#475467;margin-top:4px;font:9.5px/1.5 var(--mono);white-space:pre-wrap}
.tool-card{padding:9px 10px;border:1px solid var(--line);border-radius:9px;background:var(--panel-soft);margin:6px 0}
.tool-card strong{font-size:10.5px}.tool-card code{display:block;margin-top:4px;font:9.5px/1.55 var(--mono);color:#475467;white-space:pre-wrap}
.approval-box{border:1px dashed #d0d5dd;border-radius:10px;padding:11px;background:#fcfcfd}
.evalbox{display:grid;gap:7px;color:#475467;font-size:10.5px}

/* 用户视角补充：选项条 / 状态横幅 / 交付信息 / 待补充输入 / 配置诊断 */
.composer-opts{display:flex;flex-wrap:wrap;align-items:center;gap:16px;margin-top:12px}
.opt{display:flex;align-items:center;gap:7px;color:#475467;font-size:11.5px;font-weight:650;cursor:pointer}
.opt input{width:auto;margin:0;padding:0;accent-color:var(--accent)}
.banner{margin-top:12px;border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:9px;background:var(--panel-soft);padding:10px 12px;font-size:11.5px;line-height:1.65;color:#475467;white-space:pre-wrap}
.banner.ok{border-left-color:#22a06b;background:var(--success-soft);color:var(--success)}
.banner.warn{border-left-color:#f79009;background:var(--warn-soft);color:var(--warn)}
.banner.danger{border-left-color:#f04438;background:var(--danger-soft);color:var(--danger)}
.jobhead{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin:2px 0 0}
.jobgoal{font-size:13px;font-weight:750;color:var(--text);margin:8px 0 0;word-break:break-word}
.deliver{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:8px;margin:11px 0 4px}
.kv{border:1px solid var(--line);border-radius:10px;background:var(--panel-soft);padding:9px 10px;min-width:0}
.kv span{display:block;color:var(--muted);font-size:9.5px;font-weight:800;letter-spacing:.05em;text-transform:uppercase}
.kv strong{display:block;margin-top:3px;font-size:13px;word-break:break-word}
.kv small{display:block;margin-top:2px;color:var(--muted2);font-size:9.5px}
.asks{margin:12px 0;border:1px dashed #c7c7ff;border-radius:10px;background:#fafaff;padding:12px}
.asks ul{margin:7px 0 10px;padding-left:18px;color:#475467;font-size:11.5px}
.asks .inline-actions{margin-top:8px}
.statgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:9px;margin:2px 0 14px}
.cfg{display:grid;gap:7px;margin-top:9px}
.cfg .row{display:grid;grid-template-columns:126px minmax(0,1fr);gap:9px;font-size:11.5px;color:#475467;border-bottom:1px dashed var(--line);padding-bottom:7px}
.cfg .row b{color:var(--text)}
.cfg .row:last-child{border-bottom:0}

.toast{position:fixed;right:22px;bottom:22px;max-width:360px;padding:10px 12px;border-radius:10px;background:#101828;color:#fff;font-size:11px;box-shadow:var(--shadow-lg);z-index:99;opacity:0;transform:translateY(8px);pointer-events:none;transition:.18s}
.toast.show{opacity:1;transform:none}
button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,a:focus-visible{outline:3px solid #cfcfff;outline-offset:2px}

@media(max-width:1180px){
  .app-shell{grid-template-columns:72px minmax(0,1fr)}
  .sidebar{padding:18px 10px}.brandcopy,.navlabel,.navitem span,.sidebar-foot{display:none}
  .brand{padding:2px 6px 16px}.brandmark{width:38px}
  .navitem{justify-content:center;padding:8px}.navitem .ico{width:26px;height:26px}
  .workspace-grid,.observability-grid{grid-template-columns:1fr}
  .composer-main{grid-template-columns:1fr 150px 170px}.composer-main .btn-primary{grid-column:3}
}
@media(max-width:780px){
  .app-shell{display:block}.sidebar{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:5px;padding:9px 10px;overflow:auto}
  .brand{padding:0 8px 0 0}.brandmark{width:31px;height:31px;border-radius:9px;font-size:10px}.navgroup{display:flex;margin:0}.navitem{margin:0}.sidebar-foot{display:none}
  .main{padding:0 11px 34px}.topbar{height:62px}.topbar-title p,.local-pill{display:none}
  .composer{padding:15px}.composer-main{grid-template-columns:1fr}.composer-main .btn-primary{grid-column:auto}
  .advanced-grid,.source-grid,.metrics{grid-template-columns:1fr 1fr}
}
@media(max-width:520px){.metrics,.advanced-grid,.source-grid{grid-template-columns:1fr}.source-grid .wide{grid-column:auto}.topbar-actions .btn{display:none}.report-reader{padding:15px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style>
</head>
<body data-legacy-name="Agent Workbench">
<div class="app-shell">
<aside class="sidebar">
  <div class="brand"><div class="brandmark">MA</div><div class="brandcopy"><strong>Research Console</strong><span>Agent Workbench</span></div></div>
  <div class="navgroup">
    <div class="navlabel">工作</div>
    <a class="navitem" href="#newTask"><i class="ico">01</i><span>新建任务</span></a>
    <a class="navitem" href="#myJobs"><i class="ico">02</i><span>任务与成果</span></a>
  </div>
  <div class="navgroup">
    <div class="navlabel">系统</div>
    <a class="navitem" href="#observe"><i class="ico">03</i><span>运行观测</span></a>
    <a class="navitem" href="#statsDiag"><i class="ico">04</i><span>配置与统计</span></a>
  </div>
  <div class="sidebar-foot"><b>本地优先 · 单用户</b><br>证据可回溯 · 运行可观察 · 成本可审计</div>
</aside>

<main class="main">
  <header class="topbar">
    <div class="topbar-title"><h1>多Agent协作智能研究平台</h1><p>一次输入目标 → 自动选型执行 → 交付方式、依据、花费与等级</p></div>
    <div class="topbar-actions"><button class="btn btn-quiet" onclick="refreshAll()">刷新</button><div class="local-pill"><i class="local-dot"></i>Local Workbench</div></div>
  </header>

  <div class="content">
    <section class="panel composer" id="newResearch">
      <div class="section-head"><span class="section-num">01</span><div class="section-title"><strong>新建任务</strong><span>只写清楚要什么结果；资料、联网与预算按需展开，其余由系统按默认值推进。</span></div></div>
      <div class="composer-main">
        <label class="field grow"><span>任务描述</span><input id="task" value="帮我整理资料并生成一份带引用的研究报告" placeholder="例如：研究Agentic RAG在企业知识问答中的应用，并形成带证据的技术报告"></label>
        <label class="field"><span>模式</span><select id="mode" onchange="loadConfig()"><option value="mock">Mock 离线演示</option><option value="real">真实模型（日常使用）</option></select></label>
        <label class="field"><span>流程</span><select id="flow"><option value="research">研究写作链</option><option value="agent">通用 Agent</option></select></label>
        <button class="btn btn-primary" id="start" onclick="startRun()" disabled>开始运行</button>
      </div>
      <div class="composer-opts">
        <label class="opt" title="允许系统按需联网搜索并读取正文；未配置搜索服务时不会假装搜过"><input type="checkbox" id="allowNetwork"><span>允许联网研究</span></label>
        <label class="opt" title="只生成执行方案，不真正运行，也不产生模型费用"><input type="checkbox" id="planOnly"><span>先看计划再开始</span></label>
        <span class="small" id="composerHint">只有给了主题又没给资料时，才需要勾选“允许联网研究”。</span>
      </div>
      <div id="config">正在检查配置…</div>
      <div id="startNote" class="banner hidden"></div>
      <div class="workflow" aria-label="研究流程">
        <span class="step"><b>1</b>理解任务</span><span class="arr">→</span>
        <span class="step"><b>2</b>多Agent执行</span><span class="arr">→</span>
        <span class="step"><b>3</b>证据核验</span><span class="arr">→</span>
        <span class="step"><b>4</b>报告交付</span>
      </div>

      <details class="advanced">
        <summary>高级设置 · 资料来源 / 预算 / 写作硬约束</summary>
        <div class="advanced-grid">
          <label class="field"><span>模型调用数</span><input id="maxcalls" type="number" min="0" step="1" value="12"></label>
          <label class="field"><span>输出 Token</span><input id="maxtokens" type="number" min="0" step="1" value="8192"></label>
          <label class="field"><span>运行秒数</span><input id="maxseconds" type="number" min="0" value="600"></label>
          <label class="field"><span>估算美元阈值</span><input id="maxcost" type="number" min="0" step="0.01" value="0.15"></label>
        </div>
        <p class="hint">费用是本地参考估算；最后一笔请求可能越过阈值。时间限制在调用边界检查，不能强杀后台工具。</p>
        <div class="source-grid">
          <label class="field"><span>本地文件路径 · 每行一个</span><textarea id="filepaths" rows="3" placeholder="D:/资料/笔记.md"></textarea></label>
          <label class="field"><span>网页链接 · 每行一个</span><textarea id="urls" rows="3" placeholder="https://example.com/article"></textarea></label>
          <label class="field wide"><span>直接粘贴文本</span><textarea id="pastetext" rows="3" placeholder="整段内容会作为一份资料来源"></textarea></label>
          <label class="field"><span>必需章节 · 每行一条</span><textarea id="requireSections" rows="3" placeholder="资料目录"></textarea></label>
          <label class="field"><span>禁止表述 · 每行一条</span><textarea id="forbidClaims" rows="3" placeholder="全体参与者75%满意"></textarea></label>
          <label class="field wide"><span>关键事实 · 每行一条</span><textarea id="keyFacts" rows="3" placeholder="试点共40人"></textarea></label>
        </div>
      </details>
    </section>

    <section class="metrics" id="metricsSection" aria-label="工作台概览">
      <div class="metric"><span>任务总数</span><strong id="metricJobs">0</strong><small id="metricJobsSub">暂无任务</small></div>
      <div class="metric"><span>运行记录</span><strong id="metricRuns">0</strong><small id="metricRunsSub">暂无运行</small></div>
      <div class="metric"><span>当前模型</span><strong id="metricModel" style="font-size:13px">—</strong><small id="metricProvider">配置未检查</small></div>
      <div class="metric"><span>当前状态</span><strong id="metricStatus" style="font-size:13px">Idle</strong><small>选择任务后实时更新</small></div>
    </section>

    <div class="workspace-grid">
      <section class="panel panel-pad" id="jobsSection">
        <div class="section-head"><span class="section-num">02</span><div class="section-title"><strong>任务与成果</strong><span>点任务行查看交付等级、报告、依据来源；可停止、恢复、改稿与导出。</span></div><div class="right"><button class="btn" onclick="loadJobs()">刷新</button></div></div>
        <div id="jobnote" class="small">提交任务后在这里跟踪；默认先看到结果与依据，过程细节在“运行观测”。</div>
        <div id="jobtbl"></div>
        <div class="divider"></div>
        <div id="jobhint" class="empty"><b>还没有选择任务</b><span>点上面任意一行，查看它的交付等级、报告正文、引用原文与来源。</span></div>
        <div id="jobdetail" class="hidden">
        <div class="split-head"><div><span class="small">当前任务</span><div id="curjob" class="mono small">—</div></div><div id="jobstate"></div></div>
        <div id="jobgoal" class="jobgoal"></div>
        <div id="jobhead" class="jobhead"></div>
        <div id="jobdeliver" class="deliver"></div>
        <div id="jobasks" class="asks hidden"></div>
        <div class="progressbar"><span id="jobProgressBar"></span></div>
        <details class="stagedetails" id="jobstages">
          <summary id="jobstagesum">过程阶段 · 默认收起，需要时展开</summary>
          <div id="jobProgress" class="stage-rail"></div>
        </details>
        <div class="state-line">
          <button class="btn btn-danger" id="btncancel" onclick="cancelJob()" disabled>停止</button>
          <button class="btn" id="btnresume" onclick="resumeJob()" disabled>恢复</button>
          <button class="btn" id="btnexport" onclick="exportReport()" disabled>下载 Markdown</button>
          <a class="btn hidden" id="btnhtml" href="#" onclick="return exportHtml()">导出 HTML</a>
          <input id="revinstr" style="max-width:260px" placeholder="改稿指令，例如：缩短到150字并保留引用">
          <button class="btn" id="btnrevise" onclick="reviseJob()" disabled>追问改稿</button>
        </div>
        <div id="pendingInput" class="small"></div>
        <div id="joblog" class="console">尚未选择研究任务。</div>
        <div id="reportview" class="report-toolbar"></div>
        <div id="rtok" class="report-reader"><div class="empty"><b>报告阅读区</b>任务完成后会在这里显示最新报告，并保留证据引用交互。</div></div>
        <div id="evidenceview" class="evidence-list"></div>
        </div>
      </section>

      <section class="panel panel-pad" id="sourcesSection">
        <div class="section-head"><span class="section-num">03</span><div class="section-title"><strong>资料与产物</strong><span>交付的依据：点来源、产物或引用编号查看原文与定位。</span></div></div>
        <div id="libnote" class="small">选择任务后加载来源与产物。</div>
        <div id="srclist"></div>
        <div id="artlist" style="margin-top:10px"></div>
        <div class="divider"></div>
        <div class="small" style="margin-bottom:6px">内容 / 证据详情</div>
        <div id="docview" class="docview">点击来源、产物或证据查看详情。</div>
      </section>
    </div>

    <div class="observability-grid">
      <section class="panel panel-pad" id="runsSection">
        <div class="section-head"><span class="section-num">04</span><div class="section-title"><strong>通用 Agent 运行</strong><span>不经过研究链的直跑任务：模型用量与最终回答。</span></div><div class="right"><button class="btn" onclick="loadRuns()">刷新</button></div></div>
        <div id="runs"></div>
        <div class="divider"></div>
        <div class="split-head"><div><span class="small">当前 run</span><div id="cur" class="mono small">—</div></div><div id="status"></div></div>
        <div id="jobusage" class="small"></div>
        <div id="answer" class="docview">选择一个 run 查看最终回答。</div>
        <div class="small" style="margin:10px 0 6px">实时事件</div>
        <div id="stream" class="console"></div>
      </section>

      <div class="stack">
        <section class="panel panel-pad" id="traceSection">
          <div class="section-head"><span class="section-num">05</span><div class="section-title"><strong>执行轨迹</strong><span>过程细节默认收起在这里：LLM、工具、节点与运行事件。</span></div></div>
          <div id="plan" class="small"></div>
          <div id="trace" class="trace-list"><div class="empty"><b>暂无轨迹</b>选择一个 run 后加载。</div></div>
        </section>

        <section class="panel panel-pad" id="toolsPanel">
          <div class="section-head"><span class="section-num">06</span><div class="section-title"><strong>工具与人工审批</strong><span>工具调用记录；需要你拍板的高风险调用在这里批准或拒绝。</span></div></div>
          <div id="tools"><div class="empty"><b>暂无工具调用</b></div></div>
          <div class="divider"></div>
          <div class="approval-box">
            <div id="pending" class="small">当前无待审批调用</div>
            <div class="inline-actions" style="margin-top:8px">
              <select id="act" style="width:auto"><option value="approve">批准</option><option value="reject">拒绝</option></select>
              <button class="btn" id="decide" onclick="hitl()" disabled>提交决策</button>
              <span id="hitlmsg" class="small"></span>
            </div>
          </div>
        </section>

      </div>
    </div>

    <section class="panel panel-pad" id="statsSection" style="display:none">
      <div class="section-head"><span class="section-num">08</span><div class="section-title"><strong>使用统计</strong><span>本地任务记录实时聚合：任务数与交付等级分布。</span></div><div class="right"><button class="btn" onclick="refreshStats()">刷新统计</button></div></div>
      <div id="statsBody" class="statgrid"></div>
      <p class="small" id="statsNote">口径：本地任务表 + 各任务交付等级；费用与人工改稿分钟由你在试用日志中记录（scripts/q4_trial.ps1）。</p>
      <div class="divider"></div>
      <div class="section-head"><span class="section-num">⚙</span><div class="section-title"><strong>配置诊断</strong><span>仅检查本地配置，不发请求、不展示密钥；修改 .env 后需重启服务。</span></div><div class="right"><button class="btn" onclick="loadConfig()">检查配置</button></div></div>
      <div id="configDiag" class="cfg"><div class="row"><b>状态</b><span>点击「检查配置」查看结果。</span></div></div>
      <div class="divider"></div>
      <div class="section-head"><span class="section-num">🔒</span><div class="section-title"><strong>安全与数据边界</strong><span>说明真实模式下什么内容会离开本机。</span></div></div>
      <div class="cfg">
        <div class="row"><b>发送内容</b><span>真实模式下，任务文本、资料全文与工具结果会发送到项目在 .env 中配置的模型服务。</span></div>
        <div class="row"><b>留在本机</b><span>来源全文、证据、产物、任务账本保存在 workspaces/；密钥不进入页面、日志或产物。</span></div>
        <div class="row"><b>抓取边界</b><span>网页抓取仅 http(s)，默认拒绝私网 / 回环 / 链路本地地址，重定向逐跳复检。</span></div>
        <div class="row"><b>费用口径</b><span>费用是本地参考估算，不是账单硬封顶；默认兜底 $0.15/任务、600 秒。</span></div>
      </div>
    </section>

    <section class="panel panel-pad" id="evalSection" style="display:none">
      <div class="section-head"><span class="section-num">07</span><div class="section-title"><strong>质量评测</strong><span>离线 benchmark 报告概览（开发/复验口径，不等于你的真实任务验收）。</span></div><div class="right"><button class="btn" onclick="loadEval()">刷新</button></div></div>
      <div id="eval" class="evalbox">正在加载评测信息…</div>
    </section>
  </div>
</main>
</div>
<div id="toast" class="toast"></div>

<script>
const $=id=>document.getElementById(id);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let viewVersion=0,configVersion=0,pendingRequest=null,jobShown='',jobVersion=0,jobEvMap={},curJobId='';
let jobsCache=[],jobRequest=null,pendingAsk=null,jobLibLoaded=false;

function toast(message){
  const el=$('toast'); el.textContent=message; el.classList.add('show');
  clearTimeout(toast._timer); toast._timer=setTimeout(()=>el.classList.remove('show'),2600);
}
async function j(url,opt){
  const r=await fetch(url,opt); let d={};
  try{d=await r.json()}catch{d={error:'响应不是有效 JSON'}}
  if(!r.ok)throw new Error(d.error||('请求失败 HTTP '+r.status)); return d;
}
async function textFetch(url){
  const r=await fetch(url); if(!r.ok)throw new Error('HTTP '+r.status+'：无法读取'); return r.text();
}
function node(tag,text,cls){
  const el=document.createElement(tag); if(text!==undefined&&text!==null)el.textContent=String(text); if(cls)el.className=cls; return el;
}
function statusInfo(value){
  const s=String(value||'').toLowerCase();
  const map={
    completed:['已完成','success'],accepted:['验收通过','success'],success:['成功','success'],
    running:['运行中','accent'],queued:['排队中','info'],waiting_input:['待补充','warn'],
    waiting_human:['待审批','warn'],cancel_requested:['停止中','warn'],interrupted:['已中断','warn'],
    partial:['部分完成','warn'],draft:['草稿','warn'],failed:['失败','danger'],error:['错误','danger'],
    cancelled:['已停止','danger'],unable:['未完成','danger']
  };
  return map[s]||[value||'未知',''];
}
function badge(value){
  const [label,kind]=statusInfo(value); return node('span',label,'badge '+kind);
}
function shortId(v,n=14){v=String(v||'');return v.length>n?v.slice(0,n)+'…':v}
function fmtShort(v){const t=fmtTime(v);return t.length>10?t.slice(5):t}
function fmtTime(v){
  if(!v)return '—';
  let d;try{d=(typeof v==='number')?new Date(v*1000):new Date(v)}catch{return String(v)}
  if(!d||isNaN(d.getTime()))return String(v);
  const p=n=>String(n).padStart(2,'0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function money(v){return (v===null||v===undefined||v==='')?'未知':'$'+Number(v).toFixed(4)}
function kv(label,value,sub){
  const box=node('div',null,'kv');box.append(node('span',label),node('strong',value===undefined||value===null||value===''?'—':String(value)));
  if(sub)box.append(node('small',sub));return box;
}
function banner(id,text,kind){
  const el=$(id);if(!el)return;
  if(!text){el.className='banner hidden';el.textContent='';return}
  el.className='banner'+(kind?' '+kind:'');el.textContent=text;
}
function setMetric(id,value,subId,sub){
  $(id).textContent=value??'—'; if(subId&&$(subId))$(subId).textContent=sub??'';
}
function empty(title,detail){
  const box=node('div',null,'empty'); box.append(node('b',title)); if(detail)box.append(node('span',detail)); return box;
}
function makeTable(headers,rows){
  const wrap=node('div',null,'table-wrap'),table=document.createElement('table'),thead=document.createElement('thead'),tr=document.createElement('tr');
  headers.forEach(h=>tr.append(node('th',h)));thead.append(tr);table.append(thead);
  const tbody=document.createElement('tbody');rows.forEach(r=>tbody.append(r));table.append(tbody);wrap.append(table);return wrap;
}
function tdText(v,cls){return node('td',v??'',cls)}

async function loadConfig(){
  const version=++configVersion,mode=$('mode').value;$('start').disabled=true;$('config').textContent='正在检查配置…';
  renderConfigDiag(null);
  loadEval().catch(e=>$('eval').textContent=e.message);
  try{
    const d=await j('/api/config?mode='+encodeURIComponent(mode));if(version!==configVersion)return;
    setMetric('metricModel',d.model||'—','metricProvider',d.provider||d.mode||'—');
    if(d.ready){
      $('config').textContent=`${d.mode} / ${d.provider} / ${d.model} · 本地配置检查通过（尚未验证网络、认证或模型能力）`;
      $('start').disabled=false;
    }else{
      $('config').textContent=(d.errors||['配置不可用']).join('；');
    }
    renderConfigDiag(d);
  }catch(e){if(version===configVersion){$('config').textContent=e.message;renderConfigDiag({ready:false,errors:[e.message],mode})}}
}
function renderConfigDiag(d){
  const host=$('configDiag');if(!host)return;
  host.replaceChildren();
  const row=(k,v)=>{const r=node('div',null,'row');r.append(node('b',k),node('span',v??'—'));host.append(r)};
  if(d===null){row('状态','正在检查配置…');return}
  row('模式',d.mode||'—');
  row('模型',d.model||'—');
  row('供应商',d.provider||'—');
  row('API Key',d.key_configured===undefined?'未检查'
      :(d.mode==='mock'?'Mock 模式不需要模型 Key'
        :(d.key_configured?'已配置（不显示内容）':'未配置（真实模式不可用）')));
  row('联网搜索',d.search_provider?('已配置：'+d.search_provider):'未配置（SEARCH_PROVIDER 为空，“允许联网研究”不会假装搜过）');
  row('连接测试','未执行——静态检查不代表网络、认证或模型能力可用');
  if(d.ready===false)row('错误',(d.errors||['配置不可用']).join('；'));
}
async function startRun(){
  const lines=id=>$(id).value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
  const payload={
    task:$('task').value.trim(),mode:$('mode').value,flow:$('flow').value,max_iterations:6,
    texts:$('pastetext').value.trim()?[$('pastetext').value]:[],files:lines('filepaths'),urls:lines('urls'),
    allow_network:$('allowNetwork').checked,
    required_sections:lines('requireSections'),forbidden_claims:lines('forbidClaims'),key_facts:lines('keyFacts'),
    max_calls:Number($('maxcalls').value),max_output_tokens:Number($('maxtokens').value),
    max_seconds:Number($('maxseconds').value),max_cost:Number($('maxcost').value)
  };
  const planOnly=$('planOnly').checked;
  if(planOnly)payload.plan_only=true;
  if(!payload.task){toast('请先填写任务描述');return}
  $('start').disabled=true; $('metricStatus').textContent='Starting'; banner('startNote','正在提交任务…');
  try{
    const r=await j('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(r.status==='planned'){renderPlan(r);toast('已生成执行方案（未运行）')}
    else if(r.status==='waiting_input'){
      banner('startNote','任务已保存为“待补充”：系统还缺一个无法合理默认的条件。\n'+(r.understanding&&r.understanding.questions||[]).join('\n'),'warn');
      toast('任务待补充信息');await loadJobs();showJob(r.job_id);showView('myJobs');
    }
    else if(r.job_id){banner('startNote','任务已进入队列，可在“任务与成果”查看进度与交付。','ok');toast('任务已创建');await loadJobs();showJob(r.job_id);showView('myJobs')}
    else if(r.run_id){banner('startNote','Agent 运行已启动，可在“运行观测”查看轨迹与最终回答。','ok');toast('Agent run 已启动');await loadRuns();showView('observe');showRun(r.run_id)}
    else banner('startNote','提交完成，但没有返回任务 id。','warn');
  }catch(e){banner('startNote',e.message,'danger');toast(e.message)}
  finally{$('start').disabled=false}
}
function renderPlan(r){
  const plan=r.plan||{},subs=plan.subtasks||[],exp=plan.expected||{},budget=plan.budget||{};
  const lines=[`执行方式：${plan.mode||'—'}`, `一句理由：${plan.reason||'（方案未附理由）'}`];
  if(Object.keys(exp).length)lines.push(`预计用量：调用 ${exp.calls??'—'} 次 · 约 ${exp.cost_usd===undefined?'未知':'$'+exp.cost_usd} · ${exp.seconds??'—'} 秒`);
  if(Object.keys(budget).length)lines.push(`预算上限：${budget.max_calls??'—'} 次 · $${budget.max_cost_usd??'—'} · ${budget.max_seconds??'—'} 秒`);
  lines.push(`子任务：${subs.length} 个`);
  subs.slice(0,12).forEach((t,i)=>lines.push(`  ${i+1}. [${t.id||'-'}] ${t.description||''}${t.parallel?'（可并行）':''}`));
  lines.push('（“先看计划”只生成方案：不执行、不产生模型费用。取消勾选后再点“开始运行”才是真正执行。）');
  banner('startNote',lines.join('\n'));
}
async function refreshAll(){
  await Promise.all([loadJobs(),loadRuns()]);
  loadConfig();
  if(location.hash==='#statsDiag')renderStats();
}
async function refreshStats(){await loadJobs();renderStats()}

async function loadJobs(){
  try{
    const d=await j('/api/jobs'),jobs=d.jobs||[];jobsCache=jobs;
    const active=jobs.filter(x=>['queued','running','waiting_input','waiting_human','cancel_requested','interrupted'].includes(x.status)).length;
    setMetric('metricJobs',jobs.length,'metricJobsSub',active?`${active} 个活跃任务`:'暂无活跃任务');
    if(!jobs.length){$('jobtbl').replaceChildren(empty('暂无研究任务','到“新建任务”提交一个目标，结果与依据会回到这里。'));return}
    const rows=jobs.slice(0,20).map(r=>{
      const tr=document.createElement('tr');tr.style.cursor='pointer';
      const id=tdText('…'+String(r.job_id||'').slice(-10),'mono nowrap');id.title=r.job_id;tr.append(id);
      tr.append(tdText(r.task||'（无任务描述）','cell-main'));
      const state=document.createElement('td');state.append(badge(r.status));
      if(r.stage&&r.stage!==r.status&&!['finished','queued','waiting_input'].includes(r.stage))
        state.append(node('div',r.stage,'small'));
      tr.append(state);
      tr.append(tdText(r.message||r.error||'','cell-main'));
      tr.append(tdText(fmtShort(r.created_at),'nowrap'));
      const action=document.createElement('td'),b=node('button','查看','btn');b.onclick=()=>showJob(r.job_id);action.append(b);tr.append(action);
      tr.onclick=ev=>{if(ev.target.tagName!=='BUTTON')showJob(r.job_id)};
      return tr;
    });
    $('jobtbl').replaceChildren(makeTable(['任务 ID','目标','状态 / 阶段','消息 / 阶段说明','创建时间',''],rows));
  }catch(e){$('jobnote').textContent=e.message}
}
async function showJob(id){
  const version=++jobVersion;curJobId=id;jobEvMap={};jobShown=id;jobRequest=null;
  jobLibLoaded=false;
  $('jobhint').classList.add('hidden');$('jobdetail').classList.remove('hidden');
  $('curjob').textContent=id;$('jobstate').replaceChildren();$('pendingInput').textContent='';
  $('jobgoal').textContent='';$('jobhead').replaceChildren();$('jobdeliver').replaceChildren();
  $('jobasks').className='asks hidden';$('jobasks').replaceChildren();pendingAsk=null;
  $('btnhtml').classList.add('hidden');$('btnexport').disabled=true;
  $('joblog').textContent='正在加载任务…';$('reportview').replaceChildren();$('evidenceview').replaceChildren();
  $('rtok').replaceChildren(empty('报告阅读区','正在等待交付产物。'));
  $('libnote').textContent='任务就绪后加载来源与产物。';
  $('srclist').replaceChildren();$('artlist').replaceChildren();
  $('docview').textContent='正在加载来源与产物…';
  pollJob(id,version);
}
/* 交付信息（总计划 3.1：方式、依据、花费、等级）——只读展示，不改变执行行为 */
function renderJobHead(row,pl,jf,ledger){
  const req=(()=>{try{return JSON.parse(row.request_json||'{}')}catch{return {}}})();
  jobRequest=req;
  const goal=req.task||(pl&&pl.goal)||'';
  $('jobgoal').textContent=goal||'（无任务描述）';
  const head=$('jobhead');head.replaceChildren();
  if(req.mode)head.append(node('span',req.mode==='real'?'真实模型':'Mock 离线','badge '+(req.mode==='real'?'accent':'')));
  if(req.flow)head.append(node('span',req.flow==='research'?'研究写作链':'通用 Agent','badge'));
  if(req.delivery_kind&&req.delivery_kind!=='auto')head.append(node('span','交付：'+req.delivery_kind,'badge'));
  if(req.allow_network)head.append(node('span','允许联网','badge info'));
  if(req.orchestration&&req.orchestration!=='auto')head.append(node('span','方式：'+req.orchestration,'badge'));
  if(req.revises_job)head.append(node('span','改稿自 '+shortId(req.revises_job,10),'badge'));
  if(req.max_calls!==undefined)head.append(node('span',`上限 ${req.max_calls} 次调用 / $${req.max_cost??'—'} / ${req.max_seconds??'—'}s`,'badge'));
  const box=$('jobdeliver');box.replaceChildren();
  const level=pl&&pl.draft_level?pl.draft_level:(jf.status||row.status||'');
  box.append(kv('交付等级',statusInfo(level)[0],level||''));
  if(pl){
    box.append(kv('引用 / 未解析',`${pl.total_citations||0} / ${pl.unresolved_citations||0}`,'引用可点击核对原文'));
    box.append(kv('修订轮次',pl.revised_rounds||0));
    if(pl.delivery_kind)box.append(kv('产物类型',pl.delivery_kind));
  }
  if(ledger){
    box.append(kv('估算花费',(ledger.estimated_cost_usd===undefined||ledger.estimated_cost_usd===null)?'未知':money(ledger.estimated_cost_usd),(ledger.unknown_usage_calls?'含 '+ledger.unknown_usage_calls+' 次用量未知调用':'本地估算，非账单')));
    box.append(kv('模型调用',ledger.call_count===undefined?'—':ledger.call_count,'输出 '+(ledger.output_tokens||0)+' Token'));
  }
  if(ledger&&ledger.elapsed_seconds!==undefined)box.append(kv('用时',Number(ledger.elapsed_seconds).toFixed(1)+'s'));
  if(jf.model)box.append(kv('模型',jf.model));
  const hc=pl&&pl.hard_checks||{};
  if(hc.required_sections_total!==undefined)box.append(kv('硬约束',`章节 ${hc.required_section_hits}/${hc.required_sections_total}`,`禁语命中 ${hc.forbidden_hits} · 关键事实 ${hc.fact_hits}/${hc.fact_total}`));
}
function renderStages(stages){
  const host=$('jobProgress');host.replaceChildren();
  if(!stages||!stages.length){host.append(empty('暂无阶段记录'));$('jobProgressBar').style.width='0%';$('jobstagesum').textContent='过程阶段 · 暂无记录';return}
  let done=0;
  stages.forEach((s,i)=>{
    const st=String(s.status||'').toLowerCase(),cls=st.includes('fail')?'fail':(st.includes('run')?'running':(['ok','completed','success','accepted','done'].some(k=>st.includes(k))?'done':''));
    if(cls==='done')done++;
    const row=node('div',null,'stage '+cls),dot=node('div',cls==='done'?'✓':String(i+1),'stage-dot'),main=node('div',null,'stage-main');
    main.append(node('strong',s.stage||('阶段 '+(i+1))));main.append(node('span',s.message||s.status||''));row.append(dot,main,badge(s.status||''));host.append(row);
  });
  $('jobProgressBar').style.width=Math.round(done/stages.length*100)+'%';
  $('jobstagesum').textContent=`过程阶段 · ${done}/${stages.length} 完成（默认收起，需要时展开）`;
}
async function pollJob(id,version){
  while(version===jobVersion&&id===curJobId){
    try{
      const d=await j('/api/jobs/'+encodeURIComponent(id)+'/progress');if(version!==jobVersion)return;
      const row=d.job||{},pl=(d.pipeline||{}).result,jf=d.job_file||{},pending=d.pending_inputs||[],ledger=d.ledger||null;
      // 任务目录由执行器创建；目录就绪前不请求来源/产物，避免无意义 404
      if(!jobLibLoaded&&!d.note){jobLibLoaded=true;loadLibrary(id).catch(e=>$('libnote').textContent=e.message)}
      $('metricStatus').textContent=statusInfo(row.status)[0];
      $('jobstate').replaceChildren(row.status?badge(row.status):node('span','等待状态'));
      renderStages(pl&&pl.stages||[]);
      renderJobHead(row,pl,jf,ledger);
      const active=pending.find(x=>x.status==='pending')||null;
      $('pendingInput').textContent=active?'该任务需要补充信息才能继续——不必重跑，补充后自动排队。':'';
      renderAsk(active);
      const hc=pl&&pl.hard_checks||{};
      const lines=[];
      if(pl)lines.push(`分级: ${pl.draft_level||'-'} | 修订: ${pl.revised_rounds||0} | 引用: ${pl.total_citations||0} | 未解析: ${pl.unresolved_citations||0}`);
      if(hc.required_sections_total!==undefined)lines.push(`硬约束: 必需章节 ${hc.required_section_hits}/${hc.required_sections_total} | 禁语命中 ${hc.forbidden_hits} | 关键事实 ${hc.fact_hits}/${hc.fact_total}`);
      if(jf.error)lines.push('错误类型: '+jf.error);if(jf.message)lines.push(jf.message);
      if(ledger)lines.push(`用量: 调用 ${ledger.call_count??'—'} 次 · 输出 ${ledger.output_tokens??'—'} Token · 估算 ${ledger.estimated_cost_usd===undefined?'未知':money(ledger.estimated_cost_usd)}${ledger.unknown_usage_calls?'（含 '+ledger.unknown_usage_calls+' 次未知用量）':''}`);
      if(d.note)lines.push(d.note);
      $('joblog').textContent=lines.join('\n')||`状态: ${row.status||'未知'} / 阶段: ${row.stage||'—'}`;
      const running=['running','queued','waiting_human','waiting_input','cancel_requested','interrupted'].includes(row.status);
      $('btncancel').disabled=!running;
      $('btnresume').disabled=!['failed','partial','cancelled','interrupted'].includes(row.status);
      if(pl&&pl.draft_level){
        $('btncancel').disabled=true;$('btnresume').disabled=true;$('btnexport').disabled=false;
        await renderJobResults(id,pl);return;
      }
      $('btnexport').disabled=true;$('btnrevise').disabled=true;
      await sleep(700);
    }catch(e){
      // 刚提交的任务在 worker 建目录前还没有 job 视图：这不是失败，按"等待就绪"重试
      $('jobstate').replaceChildren(node('span','等待任务就绪','badge'));
      $('joblog').textContent='任务刚提交或目录未就绪，正在自动重试…（'+e.message+'）';
      await sleep(1000);
    }
  }
}
async function renderJobResults(id,pl){
  const a=await j('/api/jobs/'+encodeURIComponent(id)+'/artifacts');
  const reports=(a.artifacts||[]).filter(x=>['report','analysis','collection'].includes(x.kind)).sort((x,y)=>(x.version||0)-(y.version||0));
  $('btnrevise').disabled=reports.length===0;
  const ev=((await j('/api/jobs/'+encodeURIComponent(id)+'/evidence')).evidence)||[];jobEvMap={};ev.forEach(x=>jobEvMap[x.evidence_id]=x);
  const toolbar=$('reportview');toolbar.replaceChildren();
  if(reports.length){
    toolbar.append(node('span','版本','small'));
    reports.forEach(art=>{const b=node('button','v'+(art.version||'?'),'btn');b.title=art.artifact_id;b.onclick=()=>viewArtifact(id,art.artifact_id);toolbar.append(b)});
    toolbar.append(node('span','· 下载与导出见上方操作行','small'));
    $('btnhtml').classList.remove('hidden');
    await viewArtifact(id,reports[reports.length-1].artifact_id);
  }
  const host=$('evidenceview');host.replaceChildren();
  ev.slice(0,80).forEach(x=>{const b=node('button',`${x.evidence_id} ${(x.fact||'').slice(0,22)}`,'btn');b.onclick=()=>showEvidence(x);host.append(b)});
}
/* 待补充输入（D9-04）：按待输入项显示问题，补充后直接回到队列，不重跑整个任务 */
function renderAsk(active){
  const host=$('jobasks');
  if(!active){host.className='asks hidden';host.replaceChildren();pendingAsk=null;return}
  if(pendingAsk&&pendingAsk.input_id===active.input_id&&host.childElementCount)return;
  pendingAsk=active;host.className='asks';host.replaceChildren();
  host.append(node('strong','需要你补充：'));
  const ul=document.createElement('ul');
  (active.questions||[]).forEach(q=>ul.append(node('li',q)));
  host.append(ul);
  const ta=document.createElement('textarea');ta.rows=3;ta.id='asktext';ta.placeholder='例如：比较对象是方案 A 与方案 B；或在此粘贴补充资料。';
  host.append(ta);
  const row=node('div',null,'inline-actions');
  const ask=node('label',' ');ask.className='opt';
  const cb=document.createElement('input');cb.type='checkbox';cb.id='askAppend';
  ask.append(cb,node('span','把上面内容作为补充资料一起提交'));
  const btn=node('button','提交补充并继续','btn btn-primary');btn.onclick=()=>submitInput(active.input_id);
  row.append(btn,ask);host.append(row);
  host.append(node('p','任务目标与预算保持不变，旧产物保留。',"small"));
}
async function submitInput(inputId){
  const text=($('asktext')&&$('asktext').value||'').trim();
  if(!text){toast('请先填写补充内容');return}
  const base=jobRequest||{};
  const answer=($('askAppend')&&$('askAppend').checked)
    ?{texts:(Array.isArray(base.texts)?base.texts:[]).concat([text])}
    :{task:(base.task||$('task').value.trim()||'')+'；补充条件：'+text};
  try{
    await j('/api/jobs/'+encodeURIComponent(curJobId)+'/input',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answer})});
    toast('补充已提交，任务重新排队');pendingAsk=null;
    if($('jobasks'))$('jobasks').className='asks hidden';
    await loadJobs();showJob(curJobId);
  }catch(e){toast(e.message)}
}
function exportHtml(){
  if(!curJobId)return false;
  if($('btnhtml').classList.contains('hidden')){toast('当前任务还没有可导出的交付产物');return false}
  location.href='/api/jobs/'+encodeURIComponent(curJobId)+'/export.html';return false;
}
function appendCitations(container,text){
  String(text||'').split(/(\[E-\d{3}\])/g).forEach(part=>{
    if(/^\[E-\d{3}\]$/.test(part)){
      const key=part.slice(1,-1),b=node('button',part,'citebtn');b.onclick=()=>showEvidence(jobEvMap[key]||{evidence_id:key,quote:'该证据不在本任务证据库'});container.append(b);
    }else container.append(document.createTextNode(part));
  });
}
function renderReportMarkdown(md){
  const box=$('rtok');box.replaceChildren();const ulStack=[];
  String(md||'').split(/\r?\n/).forEach(line=>{
    const t=line.trimEnd();
    if(!t){box.append(node('div',''));return}
    let el;
    if(/^###\s+/.test(t)){el=node('h3');appendCitations(el,t.replace(/^###\s+/,''))}
    else if(/^##\s+/.test(t)){el=node('h2');appendCitations(el,t.replace(/^##\s+/,''))}
    else if(/^#\s+/.test(t)){el=node('h1');appendCitations(el,t.replace(/^#\s+/,''))}
    else if(/^[-*]\s+/.test(t)){let ul=box.lastElementChild;if(!ul||ul.tagName!=='UL'){ul=document.createElement('ul');box.append(ul)}el=document.createElement('li');appendCitations(el,t.replace(/^[-*]\s+/,''));ul.append(el);return}
    else{el=node('p');appendCitations(el,t)}
    box.append(el);
  });
}
async function viewArtifact(id,aid){
  try{renderReportMarkdown(await textFetch('/api/jobs/'+encodeURIComponent(id)+'/artifacts/'+encodeURIComponent(aid)+'/content'))}
  catch(e){$('jobstate').replaceChildren(badge('error'));toast(e.message)}
}
function showEvidence(x){
  const parts=[
    x.evidence_id||'',`事实：${x.fact||''}`,`标注：${x.tag||''}`,`摘录：${x.quote||''}`,
    `定位：${JSON.stringify(x.locator||{})}`,`来源：${x.source_id||''}`,`说明：${x.note||''}`
  ];$('docview').textContent=parts.join('\n');
}

async function loadLibrary(job){
  jobShown=job;const s=await j('/api/jobs/'+encodeURIComponent(job)+'/sources');
  $('libnote').textContent=`任务 ${shortId(job,18)} · 来源 ${s.total} · 可用 ${s.usable}`;
  const sources=s.sources||[];
  if(!sources.length)$('srclist').replaceChildren(empty('暂无来源'));
  else{
    const rows=sources.map(src=>{
      const tr=document.createElement('tr'),st=document.createElement('td');st.append(badge(src.status));tr.append(st);
      tr.append(tdText(src.display||src.title||src.source_id,'cell-main'));tr.append(tdText(src.byte_size||'—'));tr.append(tdText(src.status_message||(src.duplicate_of?'重复于 '+src.duplicate_of:''),'cell-main'));
      if(src.file_name){tr.style.cursor='pointer';tr.onclick=()=>showDoc('/api/jobs/'+encodeURIComponent(job)+'/sources/'+encodeURIComponent(src.source_id)+'/text')}
      return tr;
    });$('srclist').replaceChildren(makeTable(['状态','来源','字节','说明'],rows));
  }
  const a=await j('/api/jobs/'+encodeURIComponent(job)+'/artifacts'),arts=a.artifacts||[];
  if(!arts.length)$('artlist').replaceChildren(empty('暂无产物'));
  else{
    const rows=arts.map(art=>{
      const tr=document.createElement('tr');tr.append(tdText(art.artifact_id,'mono'));tr.append(tdText(art.version??'—'));tr.append(tdText(art.producer||'—'));tr.append(tdText(fmtTime(art.created_at)));
      tr.style.cursor='pointer';tr.onclick=()=>showDoc('/api/jobs/'+encodeURIComponent(job)+'/artifacts/'+encodeURIComponent(art.artifact_id)+'/content');return tr;
    });$('artlist').replaceChildren(makeTable(['产物','版本','生产者','时间'],rows));
  }
  if(String($('docview').textContent||'').indexOf('正在加载')===0)
    $('docview').textContent='点上面的来源、产物，或报告正文里的引用编号，这里显示原文与定位。';
}
async function showDoc(url){try{$('docview').textContent=await textFetch(url)}catch(e){$('docview').textContent=e.message}}

async function cancelJob(){if(!curJobId)return;try{const d=await j('/api/jobs/'+encodeURIComponent(curJobId)+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});toast(d.status==='stopped'?'任务已停止':'已请求停止')}catch(e){toast(e.message)}}
async function resumeJob(){if(!curJobId)return;try{const d=await j('/api/jobs/'+encodeURIComponent(curJobId)+'/resume',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});toast('恢复已排队：'+d.status);showJob(curJobId)}catch(e){toast(e.message)}}
async function reviseJob(){if(!curJobId)return;const instruction=$('revinstr').value.trim();if(!instruction){toast('请先填写改稿指令');return}
  try{const d=await j('/api/jobs/'+encodeURIComponent(curJobId)+'/revise',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction})});$('revinstr').value='';toast('改稿任务已排队');await loadJobs();showJob(d.job_id)}catch(e){toast(e.message)}
}
async function exportReport(){if(!curJobId)return;try{const a=await j('/api/jobs/'+encodeURIComponent(curJobId)+'/artifacts'),reports=(a.artifacts||[]).filter(x=>['report','analysis','collection'].includes(x.kind)).sort((x,y)=>(x.version||0)-(y.version||0));if(!reports.length)throw new Error('当前任务还没有可导出的报告');const latest=reports[reports.length-1];location.href='/api/jobs/'+encodeURIComponent(curJobId)+'/artifacts/'+encodeURIComponent(latest.artifact_id)+'/download'}catch(e){toast(e.message)}}

async function loadRuns(){
  try{
    const d=await j('/api/runs'),runs=d.runs||[],active=runs.filter(x=>x.status==='running'||x.status==='waiting_human').length;
    setMetric('metricRuns',runs.length,'metricRunsSub',active?`${active} 个活跃运行`:'暂无活跃运行');
    if(!runs.length){$('runs').replaceChildren(empty('暂无运行记录','通用Agent任务会显示在这里。'));return}
    const rows=runs.slice(0,20).map(r=>{
      const tr=document.createElement('tr'),id=tdText(shortId(r.run_id,16),'mono');id.title=r.run_id;tr.append(id);
      const st=document.createElement('td');st.append(badge(r.status));tr.append(st);tr.append(tdText(r.mode||'—'));tr.append(tdText(r.model||'—','cell-main'));tr.append(tdText(r.trace_count??0));
      const a=document.createElement('td'),b=node('button','查看','btn');b.onclick=()=>showRun(r.run_id);a.append(b);tr.append(a);return tr;
    });$('runs').replaceChildren(makeTable(['Run','状态','模式','模型','Trace',''],rows));
  }catch(e){$('runs').replaceChildren(empty('无法加载运行记录',e.message))}
}
async function showRun(id){
  const version=++viewVersion;$('cur').textContent=id;$('answer').textContent='正在加载…';$('stream').textContent='';$('jobusage').textContent='';pendingRequest=null;$('decide').disabled=true;$('hitlmsg').textContent='';
  poll(id,version).catch(e=>{if(version===viewVersion)toast(e.message)});
}
function renderTrace(events){
  const host=$('trace');host.replaceChildren();
  if(!events||!events.length){host.append(empty('暂无轨迹'));return}
  events.slice(-100).forEach(e=>{
    const row=node('div',null,'trace-event');row.dataset.kind=e.type||'';const dot=node('i',null,'trace-dot'),main=node('div',null,'trace-main');
    main.append(node('strong',e.type||'event'));main.append(node('span',[e.node,fmtTime(e.timestamp)].filter(Boolean).join(' · ')));
    const detail={...e};delete detail.type;delete detail.node;delete detail.timestamp;delete detail.run_id;
    const txt=JSON.stringify(detail);if(txt&&txt!=='{}')main.append(node('code',txt.slice(0,360)));row.append(dot,main);host.append(row);
  });
}
function renderTools(tools){
  const host=$('tools');host.replaceChildren();if(!tools||!tools.length){host.append(empty('暂无工具调用'));return}
  tools.forEach(x=>{const c=node('div',null,'tool-card');c.append(node('strong',x.name||'tool'));c.append(node('code',(x.result||'').slice(0,500)));if(typeof x.latency==='number')c.append(node('span',`耗时 ${x.latency}s`,'small'));host.append(c)});
}
async function poll(id,version){
  let after=0,url=`/api/runs/${encodeURIComponent(id)}`;
  while(version===viewVersion){
    const [d,meta,h,t,tc,job,plan]=await Promise.all([
      j(`${url}/events?after=${after}`),j(`${url}/meta`),j(`${url}/hitl`),j(`${url}/trace`),j(`${url}/tools`),j(`${url}/job`),j(`${url}/plan`)
    ]);
    if(version!==viewVersion)return;after=d.after;
    d.new.forEach(e=>$('stream').textContent+='['+(e.type||'event')+'] '+JSON.stringify(e).slice(0,220)+'\n');
    $('status').replaceChildren(badge(meta.status));$('metricStatus').textContent=statusInfo(meta.status)[0];$('answer').textContent=meta.final_text||'';
    const usage=job.ledger;$('jobusage').textContent=usage?`任务 ${shortId(usage.root_job_id,18)} · 模型调用 ${usage.call_count} · 输出 ${usage.output_tokens} Token · 估算 $${usage.estimated_cost_usd??'未知'} · ${usage.stop_reason||''}`:(job.note||'正在创建任务账本');
    if(usage&&usage.root_job_id&&jobShown!==usage.root_job_id)loadLibrary(usage.root_job_id).catch(()=>{});
    renderTrace(t.events||[]);renderTools(tc.tools||[]);
    $('plan').textContent=plan.tasks?`计划：${plan.tasks.length} 个任务 · ${plan.goal||''}`:(plan.note||'');
    pendingRequest=h.pending;$('decide').disabled=!pendingRequest;
    $('pending').textContent=pendingRequest?`${pendingRequest.name}\n${JSON.stringify(pendingRequest.arguments,null,2)}`:'当前无待审批调用';
    if(['completed','failed','cancelled'].includes(meta.status)&&(!usage||usage.status!=='running')){await loadRuns();return}
    await sleep(650);
  }
}
async function hitl(){
  const run=$('cur').textContent;if(!run||!pendingRequest)return;const request=pendingRequest;$('decide').disabled=true;
  try{await j('/api/hitl/decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:run,request_id:request.request_id,action:$('act').value})});pendingRequest=null;$('hitlmsg').textContent='已提交';toast('审批决策已提交')}catch(e){$('hitlmsg').textContent=e.message}
}
async function loadEval(){
  const mode=$('mode').value;try{
    const d=await j('/api/eval?mode='+encodeURIComponent(mode));if(mode!==$('mode').value)return;
    const box=$('eval');box.replaceChildren();
    box.append(node('div',`模式：${d.meta?.mode||d.mode||mode}`));box.append(node('div',`模型：${d.meta?.brain||'未记录'}`));
    if(d.totals){Object.entries(d.totals).slice(0,8).forEach(([k,v])=>box.append(node('div',`${k}: ${typeof v==='object'?JSON.stringify(v):v}`)))}
    else box.append(node('div',d.note||'尚无该模式的 benchmark 报告','muted'));
  }catch(e){$('eval').textContent=e.message}
}

loadJobs();loadRuns();loadConfig();

/* 视图路由：侧边栏每项对应独立功能视图，点谁显示谁 */
/* 视图 = 用户工作流，不是内部面板清单（依据：总计划核心体验"一次输入目标…交付时告知
   方式、依据、花费和等级"；过程细节默认收起；来源/产物属于任务详情，不独立成页） */
const VIEW_GROUPS = {
  newTask:  ["newResearch"],                                            // 一次输入目标
  myJobs:   ["metricsSection","jobsSection","sourcesSection"],          // 进度/等级/结果/引用对照/来源产物
  observe:  ["runsSection","traceSection","toolsPanel"],                // 过程记录默认收起
  statsDiag:["statsSection","evalSection"]                              // 配置诊断 / 统计 / 评测概览
};
const DEFAULT_VIEW = "newTask";
function showView(id){
  if(!VIEW_GROUPS[id]) id = DEFAULT_VIEW;
  const show = VIEW_GROUPS[id];
  Object.entries(VIEW_GROUPS).forEach(([v, secs])=>{
    secs.forEach(sec=>{
      const el=document.getElementById(sec);
      if(el) el.style.display = (show.indexOf(sec)>=0) ? "" : "none";
    });
  });
  document.querySelectorAll(".navitem").forEach(a=>{
    a.classList.toggle("active", a.getAttribute("href")==="#"+id);
  });
  if (id === "statsDiag") { renderStats(); loadConfig(); }
  if (location.hash !== "#"+id) history.replaceState(null, "", "#"+id);
  window.scrollTo(0, 0);
}
function renderStats(){
  const box = document.getElementById("statsBody");
  if (!box) return;
  const jobs = jobsCache || [];
  const active = ["queued","running","waiting_input","waiting_human","cancel_requested","interrupted"];
  const count = fn => jobs.filter(fn).length;
  const finished = jobs.filter(j => !active.includes(j.status) && j.status !== "");
  box.replaceChildren();
  box.append(kv("任务总数", jobs.length, "本地任务表，最多显示最近 50 条"));
  box.append(kv("进行中", count(j => active.includes(j.status))));
  box.append(kv("已停止 / 失败", count(j => ["cancelled","failed"].includes(j.status))));
  box.append(kv("已交付（有产物）", count(j => ["completed","partial"].includes(j.status)), "等级见各自任务详情"));
  box.append(kv("最近一次更新", jobs.length ? fmtTime(jobs[0].updated_at || jobs[0].created_at) : "—"));
  box.append(kv("等待人工", count(j => ["waiting_input","waiting_human"].includes(j.status)), "待补充信息 / 待审批"));
  const note = document.getElementById("statsNote");
  if (note) note.textContent = `口径：本地任务表实时聚合（当前 ${jobs.length} 条，其中已结束 ${finished.length} 条）；`
    + "交付等级、费用与人工改稿分钟按任务详情和试用日志（scripts/q4_trial.ps1）逐条核对——这里不把测试批次算作你的真实使用。";
}
window.addEventListener("hashchange", ()=>showView((location.hash||("#"+DEFAULT_VIEW)).slice(1)));
showView((location.hash || ("#"+DEFAULT_VIEW)).slice(1));
</script>
</body>
</html>"""


def make_server(workspaces: Path | None = None, port: int = 8765, *,
                tool_registry=None, approval_timeout: float = 300, settings=None) -> ThreadingHTTPServer:
    class BoundHandler(Handler):
        state = WorkbenchState(Path(workspaces) if workspaces else DEFAULT_WORKSPACES,
                               tool_registry, approval_timeout, settings)
    return ThreadingHTTPServer(("127.0.0.1", port), BoundHandler)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = make_server(port=args.port)
    print(f"Workbench: http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
