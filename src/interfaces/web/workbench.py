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
        # O-19：与 CLI 改稿入口同口径——analysis 类正文交付也可作为原稿，report 优先
        versions = [a for a in ArtifactStore(job_dir).list()
                    if a.get("kind") in ("report", "analysis")]
        if not versions:
            raise ValueError("原任务没有报告/分析产物，无法作为改稿原稿")
        latest = sorted(versions, key=lambda a: (a.get("kind") != "report",
                                                 a.get("version") or 0))[-1]
        base_draft = ArtifactStore(job_dir).read(latest["artifact_id"]).get("text", "")
    except ValueError:
        raise
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
            "max_calls": int(snapshot.get("max_calls") or 40),
            "max_output_tokens": int(snapshot.get("max_output_tokens") or 65536),
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

    def _search_provider(self) -> str:
        """联网搜索是否配置：settings 未注入时按模型诊断同样口径回落到项目 .env。"""
        settings = self.state.settings
        if settings is None:
            try:
                from config.settings import Settings
                settings = Settings()
            except Exception:  # noqa: BLE001 —— 配置无效时如实按"未配置"展示
                return ""
        return (getattr(settings, "search_provider", "") or "").strip()

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
        if len(segments) == 2 and segments[1] == "export":
            # P1 EX-10：三格式导出（md/txt/docx）+ EX-05 可读文件名 + TP-01/02/06 骨架。
            # 只读派生层；R8：导出内容不含链接或 URL（export_kit 内逐格式断言）。
            from src.harness.storage.export_kit import (build_export_document,
                                                        content_disposition,
                                                        render_docx, render_md,
                                                        render_txt)
            query = parse_qs(urlparse(self.path).query)
            fmt = (query.get("format") or ["md"])[0].lower()
            if fmt not in ("md", "txt", "docx"):
                return self._send(400, {"error": "format 只支持 md/txt/docx"})
            appendix = (query.get("appendix") or ["sources"])[0].lower()
            if appendix not in ("sources", "none"):
                return self._send(400, {"error": "appendix 只支持 sources/none"})
            artifact_id = (query.get("artifact_id") or [None])[0]
            try:
                doc = build_export_document(job_dir, artifact_id,
                                            appendix=appendix)
            except FileNotFoundError as e:
                return self._send(404, {"error": str(e)})
            except ValueError as e:
                return self._send(422, {"error": str(e)})
            render = {"md": render_md, "txt": render_txt, "docx": render_docx}[fmt]
            raw = render(doc)
            when = time.strftime("%Y-%m-%dT%H:%M:%S")
            disposition = content_disposition(
                doc.blocks[0].text if doc.blocks else "export", doc.meta["level"],
                doc.meta["version"], when, fmt, fallback_id=segments[0])
            ctype = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                     if fmt == "docx" else "text/plain; charset=utf-8")
            return self._send(200, raw, ctype, extra_headers={
                "Content-Disposition": disposition,
                "X-Content-Type-Options": "nosniff"})
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
            info["search_provider"] = self._search_provider()
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
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Research Console · Multi-Agent Research Platform</title>
<style>
:root{
  --bg:#f7f8fa;--surface:#fff;--surface2:#f9fafb;--surface3:#f2f4f7;
  --line:#e4e7ec;--line2:#d0d5dd;--text:#101828;--text2:#344054;--muted:#667085;--muted2:#98a2b3;
  --primary:#4f46e5;--primary2:#4338ca;--primary-soft:#eef2ff;
  --green:#067647;--green-soft:#ecfdf3;--amber:#b54708;--amber-soft:#fffaeb;--red:#b42318;--red-soft:#fef3f2;--blue:#175cd3;--blue-soft:#eff8ff;
  --shadow:0 1px 2px rgba(16,24,40,.04),0 4px 12px rgba(16,24,40,.035);
  --shadow-lg:0 16px 40px rgba(16,24,40,.08);
  --sidebar:228px;--top:64px;
  --font:Inter,"Segoe UI","PingFang SC","Microsoft YaHei UI","Microsoft YaHei",system-ui,-apple-system,sans-serif;
  --mono:"SFMono-Regular",Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text);font:14px/1.55 var(--font)}
button,input,select,textarea{font:inherit}
button{cursor:pointer}
a{color:inherit;text-decoration:none}
[hidden],.hidden{display:none!important}
::selection{background:#dfe3ff}
body{overflow-x:hidden}

.shell{min-height:100vh;display:grid;grid-template-columns:var(--sidebar) minmax(0,1fr)}
.sidebar{height:100vh;position:sticky;top:0;background:#fff;border-right:1px solid var(--line);padding:16px 12px;z-index:20;display:flex;flex-direction:column}
.brand{height:48px;display:flex;align-items:center;gap:10px;padding:0 8px;margin-bottom:12px}
.logo{width:32px;height:32px;border-radius:9px;background:linear-gradient(145deg,#111827,#475467);color:#fff;display:grid;place-items:center;font-size:11px;font-weight:800;letter-spacing:-.03em}
.brand-copy strong{display:block;font-size:13px;line-height:1.2}.brand-copy span{display:block;margin-top:2px;color:var(--muted2);font-size:10px}
.nav-label{padding:14px 10px 5px;color:var(--muted2);font-size:9px;font-weight:800;letter-spacing:.11em;text-transform:uppercase}
.nav{display:grid;gap:2px}
.nav a{display:flex;align-items:center;gap:10px;height:38px;border-radius:8px;padding:0 10px;color:#475467;font-size:12.5px;font-weight:650}
.nav a:hover{background:var(--surface2);color:var(--text)}
.nav a.active{background:var(--primary-soft);color:var(--primary)}
.nav-icon{width:20px;height:20px;border-radius:6px;display:grid;place-items:center;font:800 10px var(--mono);border:1px solid var(--line);background:#fff;color:var(--muted)}
.nav a.active .nav-icon{border-color:#c7d2fe;color:var(--primary);background:#fff}
.sidebar-bottom{margin-top:auto;padding:10px;border:1px solid var(--line);background:var(--surface2);border-radius:10px;color:var(--muted);font-size:10px}
.sidebar-bottom b{display:block;color:var(--text2);font-size:10.5px;margin-bottom:2px}

.app{min-width:0}
.topbar{height:var(--top);position:sticky;top:0;z-index:15;background:rgba(247,248,250,.88);backdrop-filter:blur(14px);border-bottom:1px solid rgba(228,231,236,.8);display:flex;align-items:center;padding:0 clamp(18px,2.3vw,34px)}
.breadcrumb{min-width:0;display:flex;align-items:center;gap:8px;color:var(--muted);font-size:11px}
.breadcrumb strong{color:var(--text);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:560px}
.top-actions{margin-left:auto;display:flex;align-items:center;gap:8px}
.connection{height:30px;display:flex;align-items:center;gap:7px;padding:0 10px;border:1px solid var(--line);background:#fff;border-radius:999px;color:var(--muted);font-size:10.5px}
.dot{width:7px;height:7px;border-radius:50%;background:#17b26a;box-shadow:0 0 0 3px #e6f9ef}

.page{display:none;padding:28px clamp(18px,2.4vw,36px) 56px}
.page.active{display:block}
.page.home-page{max-width:1180px;margin:0 auto}
.page.tasks-page{max-width:1360px;margin:0 auto}
.page.detail-page{max-width:1540px;margin:0 auto}
.page.observe-page{max-width:1500px;margin:0 auto}
.page.settings-page{max-width:1180px;margin:0 auto}
.page-title{display:flex;align-items:flex-start;gap:14px;margin-bottom:22px}
.page-title h1{font-size:23px;line-height:1.25;letter-spacing:-.03em;margin:0}
.page-title p{margin:6px 0 0;color:var(--muted);font-size:12px}
.page-title .actions{margin-left:auto}

.card{background:var(--surface);border:1px solid var(--line);border-radius:13px;box-shadow:var(--shadow)}
.card-pad{padding:18px}
.section-title{display:flex;align-items:center;gap:10px;margin-bottom:13px}
.section-title h2{margin:0;font-size:13px;letter-spacing:-.01em}
.section-title p{margin:2px 0 0;color:var(--muted);font-size:10.5px}
.section-title .grow{flex:1}
.section-title .right{margin-left:auto}
.divider{height:1px;background:var(--line);margin:16px -18px}

.btn{height:34px;border:1px solid var(--line2);border-radius:8px;background:#fff;color:var(--text2);padding:0 11px;font-size:11.5px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;gap:6px;white-space:nowrap;transition:.14s}
.btn:hover:not(:disabled){background:#f9fafb;border-color:#b8bec8}
.btn:disabled{opacity:.42;cursor:not-allowed}
.btn-primary{height:38px;background:var(--primary);border-color:var(--primary);color:#fff;padding:0 15px}
.btn-primary:hover:not(:disabled){background:var(--primary2);border-color:var(--primary2)}
.btn-danger{color:var(--red);border-color:#f3c2bd;background:#fff}
.btn-ghost{border-color:transparent;background:transparent}
.btn-small{height:28px;padding:0 8px;font-size:10px}
.icon-btn{width:34px;padding:0}
.link-btn{border:0;background:transparent;padding:0;color:var(--primary);font-size:11px;font-weight:700}

.field{display:flex;flex-direction:column;gap:5px;color:var(--text2);font-size:10.5px;font-weight:700}
.field-hint{font-weight:400;color:var(--muted2);font-size:9.5px}
input,select,textarea{width:100%;border:1px solid var(--line2);background:#fff;color:var(--text);border-radius:8px;padding:8px 10px;outline:none;transition:.14s}
input:focus,select:focus,textarea:focus{border-color:#818cf8;box-shadow:0 0 0 3px var(--primary-soft)}
textarea{resize:vertical}
.toggle{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--text2);cursor:pointer}
.toggle input{width:15px;height:15px;accent-color:var(--primary);padding:0}
kbd{font:9px var(--mono);padding:2px 5px;border:1px solid var(--line);border-bottom-color:#cdd2d9;border-radius:5px;background:#fff;color:var(--muted)}

.hero{padding:34px 0 10px;text-align:center}
.hero h1{font-size:28px;line-height:1.2;letter-spacing:-.045em;margin:0}
.hero p{max-width:680px;margin:10px auto 0;color:var(--muted);font-size:13px;line-height:1.7}
.composer{max-width:940px;margin:24px auto 0;padding:14px;border-radius:15px;background:#fff;border:1px solid #dfe3ea;box-shadow:0 12px 34px rgba(16,24,40,.07)}
.composer textarea{border:0;box-shadow:none!important;resize:none;padding:8px 9px 4px;min-height:88px;font-size:14px;line-height:1.65}
.composer textarea::placeholder{color:#98a2b3}
.composer-row{border-top:1px solid var(--line);margin-top:7px;padding-top:10px;display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.composer-spacer{flex:1}
.pill-action{height:31px;border:1px solid transparent;background:var(--surface2);border-radius:8px;padding:0 9px;color:#475467;font-size:10.5px;font-weight:700;display:inline-flex;align-items:center;gap:6px}
.pill-action:hover{background:var(--surface3)}
.pill-action.on{background:var(--primary-soft);color:var(--primary)}
.config-line{max-width:940px;margin:8px auto 0;color:var(--muted);font-size:10.5px;text-align:left}
.quick-examples{max-width:940px;margin:20px auto 0}
.quick-examples h3{font-size:10px;color:var(--muted2);text-transform:uppercase;letter-spacing:.09em;margin:0 0 8px}
.example-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.example{min-height:78px;text-align:left;padding:11px 12px;border:1px solid var(--line);border-radius:10px;background:rgba(255,255,255,.7);color:var(--text2)}
.example:hover{background:#fff;border-color:#cbd0d8}
.example strong{display:block;font-size:11px;margin-bottom:3px}.example span{font-size:10px;color:var(--muted);line-height:1.45}

.home-lower{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(260px,.7fr);gap:14px;margin-top:28px}
.recent-list{display:grid}
.recent-item{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;padding:12px 2px;border-bottom:1px solid var(--line);cursor:pointer}
.recent-item:last-child{border-bottom:0}.recent-item:hover .recent-title{color:var(--primary)}
.recent-title{font-size:11.5px;font-weight:700;color:var(--text2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.recent-meta{font-size:9.5px;color:var(--muted2);margin-top:3px}
.home-guide{display:grid;gap:10px}
.guide-step{display:grid;grid-template-columns:24px 1fr;gap:9px}
.guide-step i{width:24px;height:24px;border-radius:7px;background:var(--primary-soft);color:var(--primary);display:grid;place-items:center;font-style:normal;font:800 9px var(--mono)}
.guide-step strong{display:block;font-size:10.5px}.guide-step span{display:block;color:var(--muted);font-size:9.5px;margin-top:2px}

.badge{display:inline-flex;align-items:center;gap:5px;height:22px;border-radius:999px;padding:0 7px;background:var(--surface3);color:#475467;font-size:9.5px;font-weight:750;white-space:nowrap}
.badge:before{content:"";width:5px;height:5px;border-radius:50%;background:#98a2b3}
.badge.success{background:var(--green-soft);color:var(--green)}.badge.success:before{background:#12b76a}
.badge.warn{background:var(--amber-soft);color:var(--amber)}.badge.warn:before{background:#f79009}
.badge.danger{background:var(--red-soft);color:var(--red)}.badge.danger:before{background:#f04438}
.badge.info{background:var(--blue-soft);color:var(--blue)}.badge.info:before{background:#2e90fa}
.badge.primary{background:var(--primary-soft);color:var(--primary)}.badge.primary:before{background:#6366f1}

.task-toolbar{display:flex;align-items:center;gap:8px;margin-bottom:12px}
.searchbox{position:relative;max-width:360px;flex:1}
.searchbox input{height:34px;padding-left:31px}
.searchbox:before{content:"⌕";position:absolute;left:10px;top:5px;color:var(--muted2);font-size:17px}
.filter-chips{display:flex;gap:5px;flex-wrap:wrap}
.filter-chip{height:28px;border:1px solid var(--line);border-radius:999px;background:#fff;padding:0 9px;color:var(--muted);font-size:9.5px;font-weight:700}
.filter-chip.active{border-color:#c7d2fe;background:var(--primary-soft);color:var(--primary)}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:10px}
table{width:100%;border-collapse:collapse;min-width:760px}
th,td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle;font-size:10.5px}
th{background:var(--surface2);color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.055em;font-weight:800}
tbody tr{cursor:pointer}tbody tr:hover td{background:#fafbfc}tr:last-child td{border-bottom:0}
.task-cell{max-width:440px}.task-cell strong{display:block;color:var(--text2);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.task-cell span{display:block;color:var(--muted2);font-size:9px;margin-top:2px;font-family:var(--mono)}

.detail-head{display:flex;gap:14px;align-items:flex-start;margin-bottom:16px}
.back-btn{margin-top:2px}
.detail-main{min-width:0;flex:1}.detail-main h1{margin:0;font-size:20px;letter-spacing:-.025em;line-height:1.3;max-width:940px}
.detail-meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px;color:var(--muted);font-size:10px}
.detail-actions{margin-left:auto;display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}
.summary-strip{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;margin-bottom:14px}
.summary-box{padding:10px 12px;background:#fff;border:1px solid var(--line);border-radius:10px;min-width:0}
.summary-box span{display:block;color:var(--muted);font-size:9px;font-weight:750;text-transform:uppercase;letter-spacing:.045em}
.summary-box strong{display:block;margin-top:3px;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.summary-box small{display:block;margin-top:2px;color:var(--muted2);font-size:9px}

.progress-card{padding:12px 14px;margin-bottom:14px}
.progress-line{display:flex;align-items:center;gap:8px}
.progress-line .bar{height:5px;background:#edf0f3;border-radius:999px;overflow:hidden;flex:1}
.progress-line .bar i{display:block;height:100%;background:linear-gradient(90deg,#6366f1,#8b5cf6);width:0;transition:.2s}
.progress-label{font-size:10px;color:var(--muted);white-space:nowrap}
.stage-mini{display:flex;gap:5px;align-items:center;margin-top:9px;overflow:auto;padding-bottom:2px}
.stage-mini span{white-space:nowrap;font-size:9px;color:var(--muted);padding:4px 7px;border-radius:999px;background:var(--surface2);border:1px solid var(--line)}
.stage-mini span.done{color:var(--green);background:var(--green-soft);border-color:#abefc6}
.stage-mini span.running{color:var(--primary);background:var(--primary-soft);border-color:#c7d2fe}
.stage-mini span.fail{color:var(--red);background:var(--red-soft);border-color:#f5c3bd}

.tabs{display:flex;gap:18px;border-bottom:1px solid var(--line);margin-bottom:14px;padding:0 3px}
.tab{border:0;background:transparent;padding:10px 1px 9px;color:var(--muted);font-size:11px;font-weight:750;border-bottom:2px solid transparent}
.tab.active{color:var(--primary);border-bottom-color:var(--primary)}
.tab-panel{display:none}.tab-panel.active{display:block}

.result-layout{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:14px;align-items:start}
.report-shell{background:#fff;border:1px solid var(--line);border-radius:12px;min-width:0}
.report-top{height:48px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:7px;padding:0 12px;position:sticky;top:var(--top);background:#fff;z-index:5;border-radius:12px 12px 0 0}
.report-top .grow{flex:1}
.report-view{padding:34px clamp(22px,4vw,54px) 50px;max-width:940px;margin:0 auto;min-height:560px}
.report-view h1{font-size:25px;line-height:1.25;letter-spacing:-.03em;margin:0 0 22px}
.report-view h2{font-size:18px;margin:30px 0 10px;letter-spacing:-.015em}.report-view h3{font-size:14px;margin:22px 0 8px}
.report-view p,.report-view li{font-size:13px;line-height:1.85;color:#344054}.report-view ul,.report-view ol{padding-left:22px}
.report-view blockquote{margin:14px 0;padding:9px 12px;border-left:3px solid #c7d2fe;background:#f8f9ff;color:#475467}
.citation{border:0;background:var(--primary-soft);color:var(--primary);border-radius:5px;padding:1px 5px;margin:0 2px;font:800 9px var(--mono);vertical-align:baseline}
.citation:hover{background:#dfe3ff}
.side-stack{display:grid;gap:12px;position:sticky;top:calc(var(--top) + 14px)}
.side-card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px}
.side-card h3{margin:0 0 10px;font-size:11px}.side-card p{margin:5px 0;color:var(--muted);font-size:10px}
.evidence-box{min-height:116px;max-height:360px;overflow:auto;color:var(--text2);font-size:10.5px;white-space:pre-wrap;line-height:1.7}
.evidence-box.empty-state{display:grid;place-items:center;text-align:center;color:var(--muted2)}
.evidence-chips{display:flex;gap:5px;flex-wrap:wrap;max-height:160px;overflow:auto}
.evidence-chip{border:1px solid var(--line);background:#fff;border-radius:6px;padding:4px 6px;font-size:8.5px;font-family:var(--mono);color:var(--muted)}
.evidence-chip:hover{color:var(--primary);border-color:#c7d2fe;background:var(--primary-soft)}

.sources-layout{display:grid;grid-template-columns:360px minmax(0,1fr);gap:12px;min-height:620px}
.source-list{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}
.source-list-head{padding:11px 12px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between}
.source-items{max-height:610px;overflow:auto}
.source-item{padding:11px 12px;border-bottom:1px solid var(--line);cursor:pointer}
.source-item:last-child{border-bottom:0}.source-item:hover,.source-item.active{background:#fafbff}
.source-item strong{display:block;font-size:10.5px;color:var(--text2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.source-item span{display:block;margin-top:3px;font-size:9px;color:var(--muted)}
.source-viewer{background:#fff;border:1px solid var(--line);border-radius:12px;min-width:0}
.viewer-head{height:45px;border-bottom:1px solid var(--line);padding:0 13px;display:flex;align-items:center;gap:8px}
.viewer-body{padding:18px;white-space:pre-wrap;font:10.5px/1.75 var(--mono);color:#475467;max-height:610px;overflow:auto}

.process-grid{display:grid;grid-template-columns:minmax(0,1fr) 370px;gap:12px}
.process-card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:15px}
.process-card h3{margin:0 0 10px;font-size:11px}
.mode-card{display:flex;align-items:flex-start;gap:10px;padding:11px;background:var(--primary-soft);border-radius:9px;margin-bottom:12px}
.mode-icon{width:30px;height:30px;border-radius:8px;background:#fff;color:var(--primary);display:grid;place-items:center;font:800 10px var(--mono);flex:0 0 auto}
.mode-card strong{display:block;font-size:11px}.mode-card span{display:block;color:#5b5f8a;font-size:9.5px;margin-top:2px}
.json-box{white-space:pre-wrap;word-break:break-word;background:#101828;color:#d0d5dd;border-radius:9px;padding:11px;max-height:430px;overflow:auto;font:9.5px/1.6 var(--mono)}
.stages-vertical{display:grid;gap:8px}
.stage-row{display:grid;grid-template-columns:18px 1fr auto;gap:8px}
.stage-row i{width:18px;height:18px;border-radius:50%;display:grid;place-items:center;border:1px solid var(--line2);font:800 7px var(--mono);font-style:normal;color:var(--muted)}
.stage-row.done i{background:var(--green-soft);border-color:#abefc6;color:var(--green)}.stage-row.running i{background:var(--primary-soft);border-color:#c7d2fe;color:var(--primary)}.stage-row.fail i{background:var(--red-soft);border-color:#f5c3bd;color:var(--red)}
.stage-row strong{display:block;font-size:10px}.stage-row span{display:block;color:var(--muted);font-size:9px;margin-top:1px}

.version-list{display:grid;gap:8px}
.version-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;padding:11px 12px;border:1px solid var(--line);border-radius:9px;background:#fff}
.version-row strong{font-size:10.5px}.version-row span{display:block;color:var(--muted);font-size:9px;margin-top:2px}

.ask-box{margin-bottom:14px;padding:13px;border:1px solid #fedf89;background:var(--amber-soft);border-radius:11px}
.ask-box h3{margin:0 0 5px;color:var(--amber);font-size:11px}.ask-box ul{margin:6px 0 10px;padding-left:17px;color:#7a2e0e;font-size:10.5px}
.ask-box textarea{background:#fff}.ask-actions{display:flex;gap:7px;align-items:center;margin-top:8px;flex-wrap:wrap}

.observe-layout{display:grid;grid-template-columns:330px minmax(0,1fr);gap:12px}
.run-list{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}
.run-item{padding:10px 12px;border-bottom:1px solid var(--line);cursor:pointer}.run-item:last-child{border-bottom:0}.run-item:hover,.run-item.active{background:#fafbff}
.run-item strong{display:block;font:10px var(--mono);color:var(--text2)}.run-item span{display:block;margin-top:3px;color:var(--muted);font-size:9px}
.run-detail{display:grid;gap:12px}
.observe-tabs{display:flex;gap:6px;flex-wrap:wrap}
.observe-tab{height:28px;border:1px solid var(--line);border-radius:7px;background:#fff;padding:0 8px;color:var(--muted);font-size:9.5px;font-weight:700}
.observe-tab.active{background:var(--primary-soft);border-color:#c7d2fe;color:var(--primary)}
.console{white-space:pre-wrap;background:#101828;color:#d0d5dd;border-radius:10px;padding:12px;min-height:140px;max-height:520px;overflow:auto;font:9.5px/1.65 var(--mono)}
.trace-list{display:grid;gap:6px;max-height:520px;overflow:auto}
.trace{display:grid;grid-template-columns:7px minmax(0,1fr);gap:8px;padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:#fff}
.trace i{width:6px;height:6px;border-radius:50%;background:#98a2b3;margin-top:5px}.trace.tool i{background:#2e90fa}.trace.llm i{background:#7f56d9}.trace.run i{background:#12b76a}
.trace strong{font-size:9.5px}.trace span{display:block;color:var(--muted);font-size:8.5px;margin-top:1px}.trace code{display:block;margin-top:4px;color:#475467;white-space:pre-wrap;font:8.5px/1.5 var(--mono)}
.tool-card{padding:10px;border:1px solid var(--line);border-radius:8px;margin-bottom:7px;background:#fff}.tool-card strong{font-size:10px}.tool-card code{display:block;margin-top:4px;font:8.5px/1.6 var(--mono);color:#475467;white-space:pre-wrap}

.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.settings-grid .wide{grid-column:1/-1}
.kv-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.kv{padding:10px 11px;background:var(--surface2);border:1px solid var(--line);border-radius:8px}
.kv span{display:block;color:var(--muted);font-size:8.5px;text-transform:uppercase;letter-spacing:.05em}.kv strong{display:block;margin-top:3px;font-size:10.5px;word-break:break-word}
.notice{padding:10px 11px;border-radius:9px;background:var(--surface2);border-left:3px solid #98a2b3;color:var(--muted);font-size:10px;line-height:1.65}
.notice.ok{background:var(--green-soft);border-left-color:#12b76a;color:var(--green)}.notice.warn{background:var(--amber-soft);border-left-color:#f79009;color:var(--amber)}.notice.danger{background:var(--red-soft);border-left-color:#f04438;color:var(--red)}

.drawer-backdrop{position:fixed;inset:0;background:rgba(16,24,40,.25);z-index:49;display:none}
.drawer{position:fixed;right:0;top:0;height:100vh;width:min(520px,92vw);background:#fff;z-index:50;box-shadow:-20px 0 50px rgba(16,24,40,.12);transform:translateX(102%);transition:.2s;display:flex;flex-direction:column}
.drawer.open{transform:none}.drawer-backdrop.open{display:block}
.drawer-head{height:58px;border-bottom:1px solid var(--line);display:flex;align-items:center;padding:0 16px;gap:8px}.drawer-head strong{font-size:12px}.drawer-head .spacer{flex:1}
.drawer-body{padding:16px;overflow:auto}
.advanced-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.advanced-grid .wide{grid-column:1/-1}
.advanced-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}

.empty{padding:34px 18px;text-align:center;color:var(--muted);font-size:10.5px}
.empty strong{display:block;color:var(--text2);font-size:11px;margin-bottom:3px}
.loading{opacity:.7}
.toast{position:fixed;right:22px;bottom:22px;z-index:100;max-width:380px;background:#101828;color:#fff;border-radius:9px;padding:9px 11px;font-size:10.5px;box-shadow:var(--shadow-lg);opacity:0;transform:translateY(8px);transition:.16s;pointer-events:none}.toast.show{opacity:1;transform:none}

.mobile-nav{display:none}
@media(max-width:1180px){
  :root{--sidebar:76px}
  .brand-copy,.nav-label,.nav a span,.sidebar-bottom{display:none}
  .brand{justify-content:center;padding:0}.nav a{justify-content:center;padding:0}.nav-icon{width:28px;height:28px}
  .result-layout{grid-template-columns:minmax(0,1fr) 285px}
  .summary-strip{grid-template-columns:repeat(2,1fr)}
}
@media(max-width:900px){
  .result-layout,.sources-layout,.process-grid,.observe-layout,.settings-grid,.home-lower{grid-template-columns:1fr}
  .side-stack{position:static}.source-items{max-height:340px}.summary-strip{grid-template-columns:repeat(2,1fr)}
  .settings-grid .wide{grid-column:auto}.quick-examples .example-grid{grid-template-columns:1fr}
}
@media(max-width:720px){
  :root{--sidebar:0px}
  .shell{display:block}.sidebar{display:none}.topbar{height:56px;padding:0 14px}.connection{display:none}
  .page{padding:18px 12px 78px}.hero{padding-top:14px}.hero h1{font-size:23px}.hero p{font-size:11.5px}
  .composer{margin-top:17px}.composer-row{gap:5px}.pill-action{padding:0 7px}.composer-spacer{display:none}
  .summary-strip,.kv-grid,.advanced-grid{grid-template-columns:1fr}
  .detail-head{flex-wrap:wrap}.detail-actions{width:100%;justify-content:flex-start;margin-left:42px}
  .report-view{padding:24px 18px 36px}.report-view h1{font-size:21px}.report-top{top:56px;overflow:auto}
  .tabs{gap:14px;overflow:auto}.tab{white-space:nowrap}
  .mobile-nav{display:flex;position:fixed;left:8px;right:8px;bottom:8px;height:56px;background:rgba(255,255,255,.95);backdrop-filter:blur(12px);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow-lg);z-index:40;align-items:center;justify-content:space-around}
  .mobile-nav a{display:grid;place-items:center;color:var(--muted);font-size:9px;gap:2px}.mobile-nav a b{font:800 10px var(--mono)}.mobile-nav a.active{color:var(--primary)}
}
</style>
</head>
<body data-legacy-name="Agent Workbench">
<div class="shell">
  <aside class="sidebar">
    <div class="brand"><div class="logo">MA</div><div class="brand-copy"><strong>Research Console</strong><span>Multi-Agent Workspace</span></div></div>
    <div class="nav-label">工作</div>
    <nav class="nav">
      <a href="#/home" data-nav="home"><i class="nav-icon">⌂</i><span>首页</span></a>
      <a href="#/tasks" data-nav="tasks"><i class="nav-icon">▤</i><span>任务中心</span></a>
    </nav>
    <div class="nav-label">高级</div>
    <nav class="nav">
      <a href="#/observe" data-nav="observe"><i class="nav-icon">◎</i><span>运行观测</span></a>
      <a href="#/settings" data-nav="settings"><i class="nav-icon">⚙</i><span>配置与评测</span></a>
    </nav>
    <div class="sidebar-bottom"><b>本地单用户工作台</b>默认只展示成果与必要状态；Agent内部细节放在“运行观测”。</div>
  </aside>

  <div class="app">
    <header class="topbar">
      <div class="breadcrumb" id="breadcrumb"><span>Research Console</span><span>/</span><strong>首页</strong></div>
      <div class="top-actions">
        <button class="btn btn-ghost btn-small" onclick="refreshCurrent()">刷新</button>
        <div class="connection"><i class="dot"></i><span>127.0.0.1 · Local</span></div>
      </div>
    </header>

    <main>
      <section class="page home-page" id="page-home">
        <div class="hero">
          <h1>今天想让Agent团队完成什么？</h1>
          <p>描述目标即可。系统会判断是否需要资料、搜索和多Agent协作，并在预算与权限范围内自动推进；过程可查，但你不需要自己当项目经理。</p>
        </div>

        <div class="composer">
          <textarea id="taskEditor" rows="3" placeholder="例如：比较GraphRAG与Agentic RAG在企业知识问答中的适用场景，结合近期资料形成一份带引用的技术分析报告。"></textarea>
          <input id="task" type="hidden" value="">
          <div class="composer-row">
            <button class="pill-action" id="sourceBtn" onclick="openAdvanced('sources')">＋ 资料</button>
            <button class="pill-action" id="networkBtn" onclick="toggleNetwork()">◎ 联网 <span id="networkState">关闭</span></button>
            <button class="pill-action" id="planBtn" onclick="togglePlan()">▱ 先看计划 <span id="planState">关闭</span></button>
            <button class="pill-action" onclick="openAdvanced('execution')">⚙ 高级设置</button>
            <span class="composer-spacer"></span>
            <button class="btn btn-primary" id="start" onclick="startRun()" disabled>开始任务</button>
          </div>
        </div>
        <div class="config-line" id="config">正在检查真实模型配置…</div>

        <div class="quick-examples">
          <h3>常见任务</h3>
          <div class="example-grid">
            <button class="example" onclick="useExample('整理这批资料，输出来源清单、关键事实、冲突与信息缺口。')"><strong>整理资料</strong><span>适合本地文档、粘贴文本和指定网页。</span></button>
            <button class="example" onclick="useExample('围绕这个主题进行研究，获取可靠来源并生成带引用的分析报告。')"><strong>主题研究</strong><span>允许联网时可自动搜索、读取正文并保留来源。</span></button>
            <button class="example" onclick="useExample('比较两个方案的优缺点、证据与适用条件，并给出结构化比较分析。')"><strong>比较分析</strong><span>系统会根据任务结构选择单Agent或多Agent方式。</span></button>
          </div>
        </div>

        <div class="home-lower">
          <div class="card card-pad">
            <div class="section-title"><div class="grow"><h2>最近任务</h2><p>点击任务继续查看结果、来源和版本。</p></div><div class="right"><button class="link-btn" onclick="go('/tasks')">查看全部</button></div></div>
            <div id="recentJobs" class="recent-list"><div class="empty"><strong>正在加载任务</strong></div></div>
          </div>
          <div class="card card-pad">
            <div class="section-title"><div class="grow"><h2>工作方式</h2><p>默认由系统自动决定。</p></div></div>
            <div class="home-guide">
              <div class="guide-step"><i>1</i><div><strong>描述结果</strong><span>告诉系统你真正需要交付什么。</span></div></div>
              <div class="guide-step"><i>2</i><div><strong>自动选型</strong><span>single / fixed / fanout / manager-worker / dynamic-team / debate。</span></div></div>
              <div class="guide-step"><i>3</i><div><strong>核验交付</strong><span>结果、来源、证据、用量和局限统一进入任务详情。</span></div></div>
            </div>
          </div>
        </div>

        <div id="planPreview" class="card card-pad hidden" style="margin-top:14px">
          <div class="section-title"><div class="grow"><h2>建议执行计划</h2><p>这是“先看计划”结果，确认后再真正执行。</p></div><button class="btn" onclick="closePlanPreview()">关闭</button></div>
          <div id="planPreviewBody"></div>
          <div style="display:flex;justify-content:flex-end;margin-top:12px"><button class="btn btn-primary" onclick="runApprovedPlan()">按此方向开始执行</button></div>
        </div>
      </section>

      <section class="page tasks-page" id="page-tasks">
        <div class="page-title"><div><h1>任务中心</h1><p>这里是你的工作记录，不是内部运行日志。优先展示目标、状态、交付和更新时间。</p></div><div class="actions"><button class="btn btn-primary" onclick="go('/home')">＋ 新建任务</button></div></div>
        <div class="task-toolbar">
          <div class="searchbox"><input id="taskSearch" placeholder="搜索任务目标或Job ID" oninput="renderTaskTable()"></div>
          <div class="filter-chips" id="taskFilters">
            <button class="filter-chip active" data-filter="all" onclick="setTaskFilter('all',this)">全部</button>
            <button class="filter-chip" data-filter="active" onclick="setTaskFilter('active',this)">进行中</button>
            <button class="filter-chip" data-filter="done" onclick="setTaskFilter('done',this)">已交付</button>
            <button class="filter-chip" data-filter="attention" onclick="setTaskFilter('attention',this)">需要处理</button>
          </div>
        </div>
        <div class="card card-pad">
          <div id="jobtbl"><div class="empty"><strong>正在加载</strong></div></div>
        </div>
      </section>

      <section class="page detail-page" id="page-job">
        <div class="detail-head">
          <button class="btn icon-btn back-btn" onclick="go('/tasks')" title="返回任务中心">←</button>
          <div class="detail-main">
            <h1 id="jobTitle">研究任务</h1>
            <div class="detail-meta"><span id="jobstate"></span><span id="jobIdMeta" class="mono"></span><span id="jobTimeMeta"></span></div>
          </div>
          <div class="detail-actions">
            <button class="btn btn-danger" id="btncancel" onclick="cancelJob()" disabled>停止任务</button>
            <button class="btn" id="btnresume" onclick="resumeJob()" disabled>恢复</button>
            <button class="btn" id="btnexport" onclick="exportReport('md')" disabled>导出 Markdown</button>
            <button class="btn" id="btnexportdocx" onclick="exportReport('docx')" disabled>导出 Word</button>
            <button class="btn" id="btnexporttxt" onclick="exportReport('txt')" disabled>导出 TXT</button>
            <button class="btn hidden" id="btnhtml" onclick="exportHtml()">导出HTML</button>
          </div>
        </div>

        <div id="jobasks" class="ask-box hidden"></div>

        <div class="summary-strip">
          <div class="summary-box"><span>执行方式</span><strong id="sumMode">自动选择中</strong><small id="sumModeReason">系统根据任务结构决定</small></div>
          <div class="summary-box"><span>交付等级</span><strong id="sumGrade">—</strong><small id="sumGradeNote">完成后显示</small></div>
          <div class="summary-box"><span>来源</span><strong id="sumSources">—</strong><small id="sumSourcesNote">可核验来源</small></div>
          <div class="summary-box"><span>用量 / 费用</span><strong id="sumUsage">—</strong><small id="sumUsageNote">根任务累计</small></div>
        </div>

        <div class="card progress-card">
          <div class="progress-line"><strong id="progressStatus" style="font-size:10.5px">等待进度</strong><div class="bar"><i id="jobProgressBar"></i></div><span class="progress-label" id="progressPercent">0%</span></div>
          <div class="stage-mini" id="jobProgress"></div>
        </div>

        <div class="tabs">
          <button class="tab active" data-jobtab="result" onclick="switchJobTab('result',this)">结果</button>
          <button class="tab" data-jobtab="sources" onclick="switchJobTab('sources',this)">资料与产物</button>
          <button class="tab" data-jobtab="process" onclick="switchJobTab('process',this)">执行过程</button>
          <button class="tab" data-jobtab="versions" onclick="switchJobTab('versions',this)">版本与改稿</button>
        </div>

        <div class="tab-panel active" id="jobtab-result">
          <div class="result-layout">
            <article class="report-shell">
              <div class="report-top">
                <div id="reportview" style="display:flex;gap:5px;align-items:center;overflow:auto"></div>
                <span class="grow"></span>
                <span id="reportMeta" style="font-size:9px;color:var(--muted)"></span>
              </div>
              <div id="rtok" class="report-view"><div class="empty"><strong>报告将在这里显示</strong><span>任务运行时你可以离开此页面；回来后会继续读取持久化状态。</span></div></div>
            </article>
            <aside class="side-stack">
              <div class="side-card">
                <h3>证据核验</h3>
                <div id="docview" class="evidence-box empty-state">点击正文中的[E-001]引用查看证据原文与定位。</div>
              </div>
              <div class="side-card">
                <h3>本任务证据</h3>
                <div id="evidenceview" class="evidence-chips"></div>
              </div>
              <div class="side-card">
                <h3>任务说明</h3>
                <div id="joblog" style="font-size:9.5px;color:var(--muted);white-space:pre-wrap">等待任务状态…</div>
                <div id="pendingInput" style="margin-top:8px;font-size:9.5px;color:var(--amber)"></div>
              </div>
            </aside>
          </div>
        </div>

        <div class="tab-panel" id="jobtab-sources">
          <div class="sources-layout">
            <div class="source-list">
              <div class="source-list-head"><strong style="font-size:10.5px">来源 / 产物</strong><span id="libnote" style="font-size:9px;color:var(--muted)">—</span></div>
              <div class="source-items" id="srclist"></div>
              <div style="border-top:1px solid var(--line);padding:9px 12px;font-size:9px;color:var(--muted)">生成产物</div>
              <div class="source-items" id="artlist" style="max-height:260px"></div>
            </div>
            <div class="source-viewer">
              <div class="viewer-head"><strong id="sourceViewerTitle" style="font-size:10.5px">内容预览</strong></div>
              <div class="viewer-body" id="sourceViewer">选择左侧来源或产物查看完整内容。</div>
            </div>
          </div>
        </div>

        <div class="tab-panel" id="jobtab-process">
          <div class="process-grid">
            <div class="process-card">
              <h3>编排与任务分工</h3>
              <div class="mode-card"><div class="mode-icon">AI</div><div><strong id="processMode">等待编排记录</strong><span id="processReason">普通用户无需操作此处；这里用于解释系统为什么这样分工。</span></div></div>
              <div id="processHuman"></div>
              <details style="margin-top:12px"><summary style="cursor:pointer;font-size:10px;color:var(--muted);font-weight:700">查看原始过程记录</summary><div id="processRaw" class="json-box" style="margin-top:8px">—</div></details>
            </div>
            <div class="process-card">
              <h3>阶段记录</h3>
              <div id="processStages" class="stages-vertical"></div>
            </div>
          </div>
        </div>

        <div class="tab-panel" id="jobtab-versions">
          <div class="card card-pad">
            <div class="section-title"><div class="grow"><h2>报告版本</h2><p>旧版本不覆盖；改稿会生成新任务/新版本并保留谱系。</p></div></div>
            <div id="versionList" class="version-list"></div>
            <div class="divider"></div>
            <div class="field"><span>追问改稿</span><textarea id="revinstr" rows="3" placeholder="例如：面向技术负责人重新组织结构；缩短到1500字；保留所有引用。"></textarea></div>
            <div style="display:flex;justify-content:flex-end;margin-top:8px"><button class="btn btn-primary" id="btnrevise" onclick="reviseJob()" disabled>生成新版本</button></div>
          </div>
        </div>
      </section>

      <section class="page observe-page" id="page-observe">
        <div class="page-title"><div><h1>运行观测</h1><p>高级调试区。Trace、Tool、Plan、HITL都在这里，不干扰日常任务与报告阅读。</p></div></div>
        <div class="observe-layout">
          <div class="run-list"><div class="source-list-head"><strong style="font-size:10.5px">Run记录</strong><button class="link-btn" onclick="loadRuns()">刷新</button></div><div id="runs"></div></div>
          <div class="run-detail">
            <div class="card card-pad">
              <div class="section-title"><div class="grow"><h2 id="runTitle">选择一个Run</h2><p id="jobusage">运行用量和根任务账本会显示在这里。</p></div><span id="status"></span></div>
              <div class="observe-tabs">
                <button class="observe-tab active" data-obtab="answer" onclick="switchObserveTab('answer',this)">最终回答</button>
                <button class="observe-tab" data-obtab="trace" onclick="switchObserveTab('trace',this)">Trace</button>
                <button class="observe-tab" data-obtab="tools" onclick="switchObserveTab('tools',this)">Tools</button>
                <button class="observe-tab" data-obtab="plan" onclick="switchObserveTab('plan',this)">Plan</button>
                <button class="observe-tab" data-obtab="stream" onclick="switchObserveTab('stream',this)">事件流</button>
                <button class="observe-tab" data-obtab="hitl" onclick="switchObserveTab('hitl',this)">人工审批</button>
              </div>
            </div>
            <div class="card card-pad" id="ob-answer"><div id="answer" style="white-space:pre-wrap;color:var(--text2);font-size:11.5px;line-height:1.75">—</div></div>
            <div class="card card-pad hidden" id="ob-trace"><div id="trace" class="trace-list"></div></div>
            <div class="card card-pad hidden" id="ob-tools"><div id="tools"></div></div>
            <div class="card card-pad hidden" id="ob-plan"><div id="plan" class="json-box"></div></div>
            <div class="card card-pad hidden" id="ob-stream"><div id="stream" class="console"></div></div>
            <div class="card card-pad hidden" id="ob-hitl">
              <div id="pending" class="notice">当前无待审批调用。</div>
              <div style="display:flex;gap:7px;align-items:center;margin-top:10px">
                <select id="act" style="max-width:130px"><option value="approve">批准</option><option value="reject">拒绝</option></select>
                <button class="btn" id="decide" onclick="hitl()" disabled>提交决策</button><span id="hitlmsg" style="font-size:9px;color:var(--muted)"></span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="page settings-page" id="page-settings">
        <div class="page-title"><div><h1>配置与评测</h1><p>面向使用者展示“能不能运行”和必要的质量/模式信息，不展示密钥内容。</p></div></div>
        <div class="settings-grid">
          <div class="card card-pad">
            <div class="section-title"><div class="grow"><h2>运行配置</h2><p>修改.env后需要重启服务。</p></div><button class="btn btn-small" onclick="loadConfig()">重新检查</button></div>
            <div id="configDiag" class="kv-grid"></div>
          </div>
          <div class="card card-pad">
            <div class="section-title"><div class="grow"><h2>使用统计</h2><p>只统计本地任务表，不把测试批次冒充个人任务。</p></div></div>
            <div id="statsBody" class="kv-grid"></div>
          </div>
          <div class="card card-pad wide">
            <div class="section-title"><div class="grow"><h2>评测摘要</h2><p>用于了解当前模式的benchmark记录；具体业务质量仍应回到任务结果和人工验收。</p></div></div>
            <div id="eval">正在加载评测信息…</div>
          </div>
          <div class="card card-pad wide">
            <div class="section-title"><div class="grow"><h2>安全与数据边界</h2><p>说明真实模式下什么内容会离开本机（总计划 2.2）。</p></div></div>
            <div class="kv-grid">
              <div class="kv"><span>发送内容</span><strong>真实模式下，任务文本、资料全文与工具结果会发送到项目在 .env 中配置的模型服务。</strong></div>
              <div class="kv"><span>留在本机</span><strong>来源全文、证据、产物与任务账本保存在 workspaces/；密钥不进入页面、日志或产物。</strong></div>
              <div class="kv"><span>抓取边界</span><strong>网页抓取仅 http(s)，默认拒绝私网 / 回环 / 链路本地地址，重定向逐跳复检。</strong></div>
              <div class="kv"><span>费用口径</span><strong>费用是本地估算停止阈值，不是账单硬封顶；未知用量不会按 0 处理。</strong></div>
            </div>
          </div>
        </div>
      </section>
    </main>
  </div>
</div>

<nav class="mobile-nav">
  <a href="#/home" data-nav="home"><b>⌂</b>首页</a>
  <a href="#/tasks" data-nav="tasks"><b>▤</b>任务</a>
  <a href="#/observe" data-nav="observe"><b>◎</b>观测</a>
  <a href="#/settings" data-nav="settings"><b>⚙</b>设置</a>
</nav>

<div class="drawer-backdrop" id="drawerBackdrop" onclick="closeDrawer()"></div>
<aside class="drawer" id="advancedDrawer">
  <div class="drawer-head"><strong>任务高级设置</strong><span class="spacer"></span><button class="btn icon-btn btn-small" onclick="closeDrawer()">×</button></div>
  <div class="drawer-body">
    <div id="drawerSources">
      <div class="section-title"><div class="grow"><h2>资料来源</h2><p>本机工作台使用路径读取文件；也可输入链接或直接粘贴文本。</p></div></div>
      <div class="advanced-grid">
        <label class="field wide"><span>本地文件路径 <span class="field-hint">每行一个</span></span><textarea id="filepaths" rows="4" placeholder="D:/资料/论文.pdf&#10;D:/资料/笔记.md"></textarea></label>
        <label class="field wide"><span>指定网页 <span class="field-hint">每行一个</span></span><textarea id="urls" rows="4" placeholder="https://example.com/article"></textarea></label>
        <label class="field wide"><span>粘贴文本</span><textarea id="pastetext" rows="6" placeholder="把需要作为研究资料的内容粘贴在这里。"></textarea></label>
      </div>
    </div>
    <div id="drawerExecution" style="margin-top:22px">
      <div class="section-title"><div class="grow"><h2>执行与交付</h2><p>普通使用保持Auto即可；这些设置用于明确控制。</p></div></div>
      <div class="advanced-grid">
        <label class="field"><span>运行模式</span><select id="mode" onchange="loadConfig()"><option value="real" selected>真实模型</option><option value="mock">Mock离线演示</option></select></label>
        <label class="field"><span>运行入口</span><select id="flow"><option value="research" selected>智能研究 / 自动编排</option><option value="agent">通用Agent循环</option></select></label>
        <label class="field"><span>编排方式</span><select id="orchestration"><option value="auto" selected>Auto（推荐）</option><option value="single">Single</option><option value="fixed">Fixed</option><option value="manager_worker">Manager-Worker</option><option value="fanout">Fan-out</option><option value="dynamic_team">Dynamic Team</option><option value="debate">Debate</option></select></label>
        <label class="field"><span>交付类型</span><select id="deliveryKind"><option value="auto" selected>自动判断</option><option value="collection">资料整理</option><option value="analysis">分析</option><option value="report">报告</option></select></label>
        <label class="field"><span>最大模型调用</span><input id="maxcalls" type="number" min="0" value="40"></label>
        <label class="field"><span>最大输出Token</span><input id="maxtokens" type="number" min="0" value="65536"></label>
        <label class="field"><span>最大运行秒数</span><input id="maxseconds" type="number" min="0" value="600"></label>
        <label class="field"><span>费用阈值($)</span><input id="maxcost" type="number" min="0" step="0.01" value="0.15"></label>
      </div>
      <div class="notice" style="margin-top:10px">费用是项目本地估算停止阈值，不等于供应商账单硬封顶；未知用量不会按0处理。</div>
    </div>
    <div style="margin-top:22px">
      <div class="section-title"><div class="grow"><h2>写作硬约束</h2><p>需要程序层复验时再填写；每行一条。</p></div></div>
      <div class="advanced-grid">
        <label class="field"><span>必需章节</span><textarea id="requireSections" rows="4"></textarea></label>
        <label class="field"><span>禁止表述</span><textarea id="forbidClaims" rows="4"></textarea></label>
        <label class="field wide"><span>关键事实</span><textarea id="keyFacts" rows="4"></textarea></label>
      </div>
    </div>
    <div class="advanced-actions"><button class="btn" onclick="closeDrawer()">完成</button></div>
  </div>
</aside>

<div id="toast" class="toast"></div>

<script>
const $=id=>document.getElementById(id);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const DEMO=location.protocol==='file:';
let jobsCache=[],taskFilter='all',currentJob='',currentJobProgress=null,currentSources=[],currentArtifacts=[],currentEvidence={},currentProcess=null;
let jobPollToken=0,runPollToken=0,currentRun='',pendingRequest=null,allowNetwork=false,planOnly=false;
let configCache=null,libLoadedJob='';

const DEMO_JOBS=[
  {job_id:'job_demo001',task:'比较GraphRAG与Agentic RAG在企业知识问答中的适用场景',status:'completed',stage:'finished',created_at:'2026-09-22T08:10:00Z',updated_at:'2026-09-22T08:18:00Z'},
  {job_id:'job_demo002',task:'整理RAG幻觉检测资料并输出来源、冲突与缺口',status:'running',stage:'review:running',created_at:'2026-09-22T07:42:00Z',updated_at:'2026-09-22T08:22:00Z'},
  {job_id:'job_demo003',task:'比较六种Multi-Agent编排方式的适用条件',status:'waiting_input',stage:'waiting_input',created_at:'2026-09-21T12:20:00Z',updated_at:'2026-09-21T12:21:00Z'}
];

function toast(msg){const e=$('toast');e.textContent=msg;e.classList.add('show');clearTimeout(toast.t);toast.t=setTimeout(()=>e.classList.remove('show'),2500)}
async function api(url,opt){
  if(DEMO)return demoApi(url,opt);
  const r=await fetch(url,opt);let d={};
  try{d=await r.json()}catch{d={error:'响应不是有效JSON'}}
  if(!r.ok)throw new Error(d.error||('HTTP '+r.status));return d;
}
async function apiText(url){
  if(DEMO)return '# 示例报告\n\n这是文件预览模式。应用到项目后，这里会读取真实任务产物。[E-001]\n\n## 结论\n\n系统应该把用户目标与最终交付放在主路径，把Trace、工具调用和内部节点放到高级观测区。';
  const r=await fetch(url);if(!r.ok)throw new Error('HTTP '+r.status);return r.text();
}
async function demoApi(url,opt){
  if(url.startsWith('/api/config'))return {ready:true,mode:'real',provider:'demo-provider',model:'demo-model',key_configured:true,search_provider:'bing_scrape'};
  if(url==='/api/jobs')return {jobs:DEMO_JOBS};
  if(url.includes('/progress'))return {job:DEMO_JOBS.find(x=>url.includes(x.job_id))||DEMO_JOBS[0],pipeline:{result:{draft_level:'accepted',stages:[{stage:'evidence',status:'completed',message:'证据提取完成'},{stage:'material',status:'completed',message:'素材整理完成'},{stage:'outline',status:'completed',message:'提纲完成'},{stage:'draft',status:'completed',message:'初稿完成'},{stage:'review',status:'completed',message:'审校完成'}],total_citations:8,unresolved_citations:0,revised_rounds:1}},ledger:{call_count:9,output_tokens:6120,estimated_cost_usd:0.084},pending_inputs:[]};
  if(url.includes('/sources'))return {total:3,usable:3,sources:[{source_id:'src_001',status:'ok',title:'项目开发总计划',display:'PROJECT_MASTER_PLAN.md',byte_size:18420,file_name:'sources/src_001.md'},{source_id:'src_002',status:'ok',title:'S5工作台交付说明',display:'S5_DELIVERY.md',byte_size:5025,file_name:'sources/src_002.md'},{source_id:'src_003',status:'ok',title:'外部研究资料',display:'https://example.com',byte_size:9300,file_name:'sources/src_003.md'}]};
  if(url.includes('/artifacts'))return {artifacts:[{artifact_id:'report.v1',kind:'report',version:1,producer:'writer',created_at:'2026-09-22T08:17:00Z',file_name:'artifacts/report.v1.md'}]};
  if(url.includes('/evidence'))return {evidence:[{evidence_id:'E-001',fact:'用户不必充当项目经理',tag:'supported',quote:'用户不必充当项目经理，不必逐次决定调用哪个工具、创建几个智能体。',source_id:'src_001',locator:{line:13}}]};
  if(url.includes('/process'))return {selected_mode:'manager_worker',reason:'任务包含多个有依赖的研究子问题，需要先拆解再汇总。',status:'finished',tasks:[{id:'T1',role:'researcher',status:'completed'},{id:'T2',role:'reviewer',status:'completed'}]};
  if(url==='/api/runs')return {runs:[]};
  if(url.startsWith('/api/eval'))return {mode:'real',note:'预览模式不加载真实评测报告'};
  return {};
}
function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined&&text!==null)e.textContent=String(text);if(cls)e.className=cls;return e}
function short(v,n=18){v=String(v||'');return v.length>n?v.slice(0,n)+'…':v}
/* 队列时间戳是 Unix 秒（float），产物/轨迹时间是 ISO 字符串——都按本地时间显示 */
function fmtTime(v){
  if(!v)return '—';
  let d;try{d=(typeof v==='number')?new Date(v*1000):new Date(v)}catch{return String(v)}
  if(!d||isNaN(d.getTime()))return String(v);
  const p=n=>String(n).padStart(2,'0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function money(v){return v===null||v===undefined?'未知':'$'+Number(v).toFixed(3)}
function lines(id){return ($(id)?.value||'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean)}
function statusInfo(s){
  s=String(s||'').toLowerCase();
  const m={completed:['已交付','success'],partial:['待完善','warn'],accepted:['成品','success'],draft:['草稿','warn'],running:['运行中','primary'],queued:['排队中','info'],waiting_input:['待补充','warn'],waiting_human:['待审批','warn'],cancel_requested:['停止中','warn'],interrupted:['已中断','warn'],cancelled:['已停止','danger'],failed:['失败','danger'],unable:['无法完成','danger'],success:['成功','success']};
  return m[s]||[s||'未知',''];
}
function badge(s){const [t,c]=statusInfo(s);return node('span',t,'badge '+c)}
function isActive(s){return ['queued','running','waiting_input','waiting_human','cancel_requested','interrupted'].includes(s)}
function isAttention(s){return ['waiting_input','waiting_human','failed','partial','interrupted'].includes(s)}
function empty(title,sub=''){const e=node('div',null,'empty');e.append(node('strong',title));if(sub)e.append(node('span',sub));return e}
function kv(label,value){const e=node('div',null,'kv');e.append(node('span',label),node('strong',value??'—'));return e}

function go(path){location.hash='#'+path}
function route(){
  const raw=(location.hash||'#/home').slice(1),parts=raw.split('/').filter(Boolean),name=parts[0]||'home';
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('[data-nav]').forEach(a=>a.classList.toggle('active',a.dataset.nav===name||(name==='job'&&a.dataset.nav==='tasks')));
  let title='首页';
  if(name==='tasks'){showPage('tasks');title='任务中心';loadJobs()}
  else if(name==='job'&&parts[1]){showPage('job');title='任务详情';openJob(parts[1])}
  else if(name==='observe'){showPage('observe');title='运行观测';loadRuns()}
  else if(name==='settings'){showPage('settings');title='配置与评测';loadConfig();renderStats();loadEval()}
  else{showPage('home');title='首页';loadJobs()}
  $('breadcrumb').innerHTML='';$('breadcrumb').append(node('span','Research Console'),node('span','/'),node('strong',title));
  window.scrollTo(0,0);
}
function showPage(name){$('page-'+name)?.classList.add('active')}
window.addEventListener('hashchange',route);

function useExample(t){$('taskEditor').value=t;$('task').value=t;$('taskEditor').focus()}
$('taskEditor').addEventListener('input',e=>$('task').value=e.target.value);
function toggleNetwork(){allowNetwork=!allowNetwork;$('networkState').textContent=allowNetwork?'开启':'关闭';$('networkBtn').classList.toggle('on',allowNetwork)}
function togglePlan(){planOnly=!planOnly;$('planState').textContent=planOnly?'开启':'关闭';$('planBtn').classList.toggle('on',planOnly)}
function openAdvanced(section){$('advancedDrawer').classList.add('open');$('drawerBackdrop').classList.add('open');setTimeout(()=>$(section==='sources'?'drawerSources':'drawerExecution').scrollIntoView({block:'start'}),30)}
function closeDrawer(){$('advancedDrawer').classList.remove('open');$('drawerBackdrop').classList.remove('open')}

async function loadConfig(){
  const mode=$('mode').value;$('start').disabled=true;$('config').textContent='正在检查'+(mode==='real'?'真实模型':'Mock')+'配置…';
  try{
    const d=await api('/api/config?mode='+encodeURIComponent(mode));configCache=d;
    const search=d.search_provider?` · 搜索 ${d.search_provider}`:' · 未配置搜索服务';
    if(d.ready){$('config').textContent=`${d.provider||mode} / ${d.model||'—'}${search}`;$('start').disabled=false}
    else{$('config').textContent=(d.errors||['配置不可用']).join('；')+search}
    renderConfigDiag(d);
  }catch(e){$('config').textContent=e.message;renderConfigDiag({ready:false,errors:[e.message]})}
}
function renderConfigDiag(d){
  const host=$('configDiag');if(!host)return;host.replaceChildren();
  host.append(kv('状态',d?.ready?'可运行':'不可用'),kv('模式',d?.mode||$('mode').value),kv('Provider',d?.provider||'—'),kv('模型',d?.model||'—'),kv('API Key',d?.key_configured===true?'已配置（已隐藏）':d?.key_configured===false?'未配置':'未检查'),kv('搜索',d?.search_provider||'未配置'));
}

async function startRun(){
  const task=$('taskEditor').value.trim();$('task').value=task;if(!task){toast('先写清楚你希望交付的结果');return}
  const payload={
    task,mode:$('mode').value,flow:$('flow').value,orchestration:$('orchestration').value,delivery_kind:$('deliveryKind').value,
    allow_network:allowNetwork,plan_only:planOnly,texts:$('pastetext').value.trim()?[$('pastetext').value]:[],files:lines('filepaths'),urls:lines('urls'),
    required_sections:lines('requireSections'),forbidden_claims:lines('forbidClaims'),key_facts:lines('keyFacts'),
    max_calls:Number($('maxcalls').value),max_output_tokens:Number($('maxtokens').value),max_seconds:Number($('maxseconds').value),max_cost:Number($('maxcost').value)
  };
  $('start').disabled=true;
  try{
    const d=await api('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(d.status==='planned'){renderPlanPreview(d);return}
    if(d.job_id){toast(d.status==='waiting_input'?'需要补充关键信息':'任务已创建');await loadJobs();go('/job/'+d.job_id);return}
    if(d.run_id){toast('Agent运行已开始');go('/observe');setTimeout(()=>showRun(d.run_id),100);return}
    toast('任务已提交');
  }catch(e){toast(e.message)}
  finally{$('start').disabled=!configCache?.ready}
}
function renderPlanPreview(d){
  const box=$('planPreview');box.classList.remove('hidden');const b=$('planPreviewBody');b.replaceChildren();
  const plan=d.plan||{},meta=d.meta||{};
  const mode=meta.mode||meta.selected_mode||plan.mode||'auto';
  const n=node('div',null,'mode-card');n.append(node('div','AI','mode-icon'));
  const c=node('div');c.append(node('strong','建议方式：'+modeLabel(mode)),node('span',meta.reason||plan.reason||'系统根据任务结构、来源与预算选择执行方式。'));n.append(c);b.append(n);
  const subs=plan.tasks||plan.subtasks||[];
  if(Array.isArray(subs)&&subs.length){
    const list=node('div',null,'stages-vertical');subs.forEach((t,i)=>{const r=node('div',null,'stage-row');r.append(node('i',String(i+1)));const x=node('div');x.append(node('strong',t.description||t.task||t.id||('子任务'+(i+1))),node('span',[t.role,(t.covers_sections||[]).length?('覆盖章节 '+[].concat(t.covers_sections).join('、')):'',t.depends_on&&t.depends_on.length&&('依赖 '+[].concat(t.depends_on).join(','))].filter(Boolean).join(' · ')));r.append(x);list.append(r)});b.append(list)
    const budget=plan.budget||{},exp=plan.expected||{};
    if(Object.keys(exp).length||Object.keys(budget).length){
      const t=[];if(exp.calls!==undefined)t.push(`预计调用 ${exp.calls} 次`);if(exp.cost_usd!==undefined)t.push(`约 $${exp.cost_usd}`);
      if(budget.max_calls!==undefined)t.push(`上限 ${budget.max_calls} 次 / $${budget.max_cost_usd??'—'} / ${budget.max_seconds??'—'} 秒`);
      if(t.length)b.append(node('div','预算与预计用量：'+t.join(' · '),'notice'))
    }
  }else b.append(node('div',JSON.stringify(plan,null,2),'json-box'));
  box.scrollIntoView({behavior:'smooth',block:'center'});
}
function closePlanPreview(){$('planPreview').classList.add('hidden')}
function runApprovedPlan(){planOnly=false;$('planState').textContent='关闭';$('planBtn').classList.remove('on');closePlanPreview();startRun()}

async function loadJobs(){
  try{const d=await api('/api/jobs');jobsCache=d.jobs||[];renderRecentJobs();renderTaskTable();renderStats()}
  catch(e){if($('recentJobs'))$('recentJobs').replaceChildren(empty('任务加载失败',e.message));if($('jobtbl'))$('jobtbl').replaceChildren(empty('任务加载失败',e.message))}
}
function renderRecentJobs(){
  const host=$('recentJobs');if(!host)return;host.replaceChildren();
  const rows=jobsCache.slice(0,6);if(!rows.length){host.append(empty('还没有任务','从上方输入目标开始第一次研究。'));return}
  rows.forEach(j=>{const r=node('div',null,'recent-item');r.onclick=()=>go('/job/'+j.job_id);const a=node('div');a.append(node('div',j.task||j.message||'未命名任务','recent-title'),node('div',`${short(j.job_id)} · ${fmtTime(j.updated_at||j.created_at)}`,'recent-meta'));const b=document.createElement('div');b.append(badge(j.status));r.append(a,b);host.append(r)})
}
function setTaskFilter(f,el){taskFilter=f;document.querySelectorAll('.filter-chip').forEach(x=>x.classList.toggle('active',x===el));renderTaskTable()}
function renderTaskTable(){
  const host=$('jobtbl');if(!host)return;const q=($('taskSearch')?.value||'').toLowerCase().trim();
  let rows=jobsCache.filter(j=>!q||String(j.task||'').toLowerCase().includes(q)||String(j.job_id||'').toLowerCase().includes(q));
  if(taskFilter==='active')rows=rows.filter(j=>isActive(j.status));
  if(taskFilter==='done')rows=rows.filter(j=>['completed','partial'].includes(j.status));
  if(taskFilter==='attention')rows=rows.filter(j=>isAttention(j.status));
  if(!rows.length){host.replaceChildren(empty('没有匹配的任务'));return}
  const wrap=node('div',null,'table-wrap'),tbl=document.createElement('table'),thead=document.createElement('thead'),tr=document.createElement('tr');
  ['任务','状态','阶段','更新时间',''].forEach(h=>tr.append(node('th',h)));thead.append(tr);tbl.append(thead);const tb=document.createElement('tbody');
  rows.forEach(j=>{const r=document.createElement('tr');r.onclick=()=>go('/job/'+j.job_id);
    const t=document.createElement('td');t.className='task-cell';t.append(node('strong',j.task||j.message||'未命名任务'),node('span',j.job_id));r.append(t);
    const s=document.createElement('td');s.append(badge(j.status));r.append(s);r.append(node('td',stageLabel(j.stage||'—')));r.append(node('td',fmtTime(j.updated_at||j.created_at)));const a=document.createElement('td');a.append(node('span','查看 →','link-btn'));r.append(a);tb.append(r)
  });tbl.append(tb);wrap.append(tbl);host.replaceChildren(wrap)
}

async function openJob(id){
  if(currentJob===id&&currentJobProgress)return;
  currentJob=id;jobPollToken++;const token=jobPollToken;currentEvidence={};currentSources=[];currentArtifacts=[];currentProcess=null;libLoadedJob='';
  const fromList=jobsCache.find(x=>x.job_id===id);$('jobTitle').textContent=fromList?.task||'研究任务';$('jobIdMeta').textContent=id;$('jobTimeMeta').textContent=fromList?fmtTime(fromList.updated_at||fromList.created_at):'';
  $('rtok').replaceChildren(empty('正在读取任务结果'));$('docview').textContent='点击正文中的引用查看证据。';$('sourceViewer').textContent='选择左侧来源或产物查看完整内容。';
  switchJobTab('result',document.querySelector('[data-jobtab="result"]'));
  pollJob(id,token);
}
/* 补充/恢复/刷新后要重新进入同一个任务：清掉"已加载"标记，否则 openJob 会直接返回、轮询不再重启 */
function reopenCurrentJob(){if(!currentJob)return;currentJobProgress=null;jobPollToken++;openJob(currentJob)}
/* 任务目录由执行器创建：目录就绪前不请求来源/产物，避免刚提交就报 404 */
async function loadJobData(job){
  if(currentJobProgress&&currentJobProgress.note)return;
  libLoadedJob=job;await Promise.allSettled([loadLibrary(job),loadProcess(job)])
}
async function pollJob(id,token){
  while(token===jobPollToken&&id===currentJob){
    try{
      const d=await api('/api/jobs/'+encodeURIComponent(id)+'/progress');if(token!==jobPollToken)return;currentJobProgress=d;
      renderJobProgress(d);const st=d.job?.status||'';
      if(!d.note&&libLoadedJob!==id)await loadJobData(id);
      if(!isActive(st)){await renderJobResults(id);return}
      await sleep(900);
    }catch(e){$('joblog').textContent='任务状态读取失败：'+e.message;await sleep(1300)}
  }
}
function renderJobProgress(d){
  const row=d.job||{},pl=d.pipeline?.result||{},jf=d.job_file||{},ledger=d.ledger||{},pending=d.pending_inputs||[];
  $('jobTitle').textContent=row.task||jobsCache.find(x=>x.job_id===currentJob)?.task||$('jobTitle').textContent;
  $('jobstate').replaceChildren(badge(row.status));$('jobTimeMeta').textContent=fmtTime(row.updated_at||row.created_at);
  $('sumGrade').textContent=pl.draft_level?statusInfo(pl.draft_level)[0]:'运行中';$('sumGradeNote').textContent=pl.unresolved_citations!==undefined?`未解析引用 ${pl.unresolved_citations}`:'完成后显示';
  $('sumUsage').textContent=ledger.call_count!==undefined?`${ledger.call_count}次 · ${money(ledger.estimated_cost_usd)}`:'—';$('sumUsageNote').textContent=ledger.output_tokens!==undefined?`${ledger.output_tokens} 输出Token`:'根任务累计';
  const stages=pl.stages||[];renderStages(stages,row.stage);
  const active=pending.find(x=>x.status==='pending');renderAsk(active);$('pendingInput').textContent=active?'该任务需要你补充关键信息后继续。':'';
  const lines2=[];if(pl.draft_level)lines2.push(`交付等级：${pl.draft_level}`);if(pl.total_citations!==undefined)lines2.push(`引用 ${pl.total_citations} · 未解析 ${pl.unresolved_citations||0} · 修订 ${pl.revised_rounds||0}`);if(jf.message)lines2.push(jf.message);if(d.note)lines2.push(d.note);$('joblog').textContent=lines2.join('\n')||`状态：${row.status||'—'} · 阶段：${row.stage||'—'}`;
  $('btncancel').disabled=!isActive(row.status);$('btnresume').disabled=!['failed','partial','cancelled','interrupted'].includes(row.status);
}
function renderStages(stages,currentStage){
  const host=$('jobProgress');host.replaceChildren();let done=0,total=stages.length||1;
  if(stages.length){stages.forEach(s=>{const st=String(s.status||'').toLowerCase(),cls=st.includes('fail')?'fail':st.includes('run')?'running':['completed','done','success','accepted','ok'].some(k=>st.includes(k))?'done':'';if(cls==='done')done++;host.append(node('span',stageLabel(s.stage||'阶段'),cls))})}
  else{const fallback=['理解任务','规划与选型','资料与执行','核验','交付'];fallback.forEach((x,i)=>host.append(node('span',x,i===0&&currentStage?'running':'')));done=0;total=fallback.length}
  const pct=Math.round(done/total*100);$('jobProgressBar').style.width=pct+'%';$('progressPercent').textContent=pct+'%';$('progressStatus').textContent=currentStage?stageLabel(currentStage):((pct===100)?'已完成':'执行中')
}
/* 阶段/状态词按用户可读中文展示（后端 stage 里会出现 finished、waiting_input 等内部词） */
function stageLabel(v){
  const m={finished:'已结束',queued:'排队中',running:'执行中',waiting_input:'待补充',waiting_human:'待审批',
           evidence:'证据提取',material:'素材整理',outline:'提纲规划',draft:'初稿写作',review:'审校',
           cancel_requested:'停止中',interrupted:'已中断'};
  const s=String(v||'');return m[s]||s
}
function renderAsk(active){
  const host=$('jobasks');host.replaceChildren();if(!active){host.classList.add('hidden');return}host.classList.remove('hidden');host.append(node('h3','还需要你补充一点信息'));
  const ul=document.createElement('ul');(active.questions||[]).forEach(q=>ul.append(node('li',q)));host.append(ul);
  const ta=document.createElement('textarea');ta.rows=3;ta.id='asktext';ta.placeholder='直接回答上面的问题，或补充需要纳入任务的资料。';host.append(ta);
  const actions=node('div',null,'ask-actions');const btn=node('button','提交并继续','btn btn-primary');btn.onclick=()=>submitInput(active.input_id);
  const lab=node('label',null,'toggle'),cb=document.createElement('input');cb.type='checkbox';cb.id='askAppend';lab.append(cb,node('span','把回答作为补充资料'));actions.append(btn,lab);host.append(actions)
}
async function submitInput(inputId){
  const text=($('asktext')?.value||'').trim();if(!text){toast('请先填写补充内容');return}
  // 合并进原请求快照：作为补充资料时追加到原有 texts，不能丢掉提交时粘贴的材料
  let base={};try{base=JSON.parse(currentJobProgress?.job?.request_json||'{}')}catch{base={}}
  const answer=$('askAppend')?.checked
    ?{texts:(Array.isArray(base.texts)?base.texts:[]).concat([text])}
    :{task:(base.task||$('jobTitle').textContent)+'；补充条件：'+text};
  try{
    await api('/api/jobs/'+encodeURIComponent(currentJob)+'/input',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answer})});
    toast('已补充，任务会继续执行');$('jobasks').classList.add('hidden');reopenCurrentJob()
  }catch(e){toast(e.message)}
}
async function renderJobResults(id){
  await loadJobData(id);
  const reports=currentArtifacts.filter(a=>['report','analysis','collection'].includes(a.kind)).sort((a,b)=>(a.version||0)-(b.version||0));
  $('btnexport').disabled=!reports.length;$('btnexportdocx').disabled=!reports.length;$('btnexporttxt').disabled=!reports.length;$('btnhtml').classList.toggle('hidden',!reports.length);$('btnrevise').disabled=!reports.length;
  const toolbar=$('reportview');toolbar.replaceChildren();
  if(reports.length){toolbar.append(node('span','版本','badge'));reports.forEach(a=>{const b=node('button','v'+(a.version||'?'),'btn btn-small');b.onclick=()=>viewArtifact(id,a.artifact_id);toolbar.append(b);const d=node('button','导出','btn btn-small');d.title='导出这一版本（Markdown）';d.onclick=()=>location.href='/api/jobs/'+encodeURIComponent(currentJob)+'/export?format=md&artifact_id='+encodeURIComponent(a.artifact_id);toolbar.append(d)});await viewArtifact(id,reports[reports.length-1].artifact_id)}
  else $('rtok').replaceChildren(empty('没有报告产物','该任务可能失败、取消，或交付类型不是报告。'));
  renderVersions();
}
async function loadLibrary(job){
  const [s,a,e]=await Promise.all([api('/api/jobs/'+encodeURIComponent(job)+'/sources'),api('/api/jobs/'+encodeURIComponent(job)+'/artifacts'),api('/api/jobs/'+encodeURIComponent(job)+'/evidence')]);
  currentSources=s.sources||[];currentArtifacts=a.artifacts||[];currentEvidence={};(e.evidence||[]).forEach(x=>currentEvidence[x.evidence_id]=x);
  $('sumSources').textContent=`${s.usable??0}/${s.total??0}`;$('sumSourcesNote').textContent='可用 / 总来源';$('libnote').textContent=`${s.usable??0}可用 / ${s.total??0}总计`;
  renderSourceList();renderEvidenceChips();renderVersions()
}
function renderSourceList(){
  const sHost=$('srclist');sHost.replaceChildren();if(!currentSources.length)sHost.append(empty('暂无来源'));
  currentSources.forEach(s=>{const r=node('div',null,'source-item');r.dataset.sid=s.source_id||'';r.onclick=()=>viewSource(s,r);r.append(node('strong',s.display||s.title||s.source_id));const m=node('span');m.append(badge(s.status),document.createTextNode(' '+(s.source_id||'')));r.append(m);const url=sourceUrl(s);if(url){const open=node('button','打开网页 ↗','btn btn-small');open.title='回原始出处（以本机存档全文为权威依据）';open.onclick=(ev)=>{ev.stopPropagation();openSourceLink(url)};r.append(open)}if(s.withdrawn_at){r.append(node('span','已撤回 '+String(s.withdrawn_at).slice(0,10),'small'))}sHost.append(r)});
  const aHost=$('artlist');aHost.replaceChildren();if(!currentArtifacts.length)aHost.append(empty('暂无产物'));
  currentArtifacts.forEach(a=>{const r=node('div',null,'source-item');r.onclick=()=>viewArtifactInSource(a,r);r.append(node('strong',a.artifact_id));r.append(node('span',`${a.kind||'artifact'} · v${a.version||'?'} · ${a.producer||'—'}`));aHost.append(r)})
}
async function viewSource(s,el){document.querySelectorAll('.source-item').forEach(x=>x.classList.remove('active'));if(el)el.classList.add('active');$('sourceViewerTitle').textContent=s.display||s.title||s.source_id;if(!s.file_name){$('sourceViewer').textContent=s.status_message||'该来源没有可读取正文。';return}try{$('sourceViewer').textContent=await apiText('/api/jobs/'+encodeURIComponent(currentJob)+'/sources/'+encodeURIComponent(s.source_id)+'/text')}catch(e){$('sourceViewer').textContent=e.message}}
async function viewArtifactInSource(a,el){document.querySelectorAll('.source-item').forEach(x=>x.classList.remove('active'));el.classList.add('active');$('sourceViewerTitle').textContent=a.artifact_id;try{$('sourceViewer').textContent=await apiText('/api/jobs/'+encodeURIComponent(currentJob)+'/artifacts/'+encodeURIComponent(a.artifact_id)+'/content')}catch(e){$('sourceViewer').textContent=e.message}}
function renderEvidenceChips(){const h=$('evidenceview');h.replaceChildren();Object.values(currentEvidence).slice(0,80).forEach(e=>{const b=node('button',e.evidence_id,'evidence-chip');b.title=(e.fact||e.quote||'').slice(0,120);b.onclick=()=>showEvidence(e);h.append(b)});if(!h.childElementCount)h.append(node('span','暂无证据','small'))}
function showEvidence(e){$('docview').classList.remove('empty-state');$('docview').replaceChildren();const box=node('div');box.append(node('strong',e.evidence_id||''));box.append(node('div',[e.fact&&('事实：'+e.fact),e.tag&&('标注：'+e.tag),e.quote&&('原文：'+e.quote),e.source_id&&('来源：'+e.source_id),e.locator&&('定位：'+(e.locator.paragraph?('第 '+e.locator.paragraph+' 段'):(e.locator.page?('第 '+e.locator.page+' 页'):''))),e.note&&('说明：'+e.note)].filter(Boolean).join('\n')));const src=currentSources.find(s=>s.source_id===e.source_id)||null;const actions=node('div');actions.style.marginTop='6px';const viewBtn=node('button','查看已保存全文','btn btn-small');viewBtn.onclick=()=>focusSource(e);actions.append(viewBtn);const url=src?sourceUrl(src):'';if(url){const openBtn=node('button','打开原始网页 ↗','btn btn-small');openBtn.title=String(url);openBtn.onclick=()=>openSourceLink(url);actions.append(openBtn)}else{actions.append(node('span','（本地/粘贴来源以本机存档全文为权威依据）','small'))}box.append(actions);$('docview').append(box)}
async function focusSource(e){switchJobTab('sources',document.querySelector('[data-jobtab="sources"]'));const src=currentSources.find(s=>s.source_id===e.source_id);if(!src){toast('证据库未找到该来源');return}await viewSource(src,document.querySelector('.source-item[data-sid="'+(e.source_id||'')+'"]'));const quote=e.quote||'';const viewer=$('sourceViewer');if(quote&&viewer&&viewer.textContent.includes(quote)){const parts=viewer.textContent.split(quote);viewer.replaceChildren();parts.forEach((p,i)=>{viewer.append(document.createTextNode(p));if(i<parts.length-1){const m=node('mark',quote);viewer.append(m)}})}}
function renderReportMarkdown(md){
  const box=$('rtok');box.replaceChildren();
  String(md||'').split(/\r?\n/).forEach(line=>{
    const t=line.trimEnd();if(!t){box.append(node('div',''));return}
    let el;
    if(/^###\s+/.test(t)){el=node('h3');appendCites(el,t.replace(/^###\s+/,''))}
    else if(/^##\s+/.test(t)){el=node('h2');appendCites(el,t.replace(/^##\s+/,''))}
    else if(/^#\s+/.test(t)){el=node('h1');appendCites(el,t.replace(/^#\s+/,''))}
    else if(/^[-*]\s+/.test(t)){let ul=box.lastElementChild;if(!ul||ul.tagName!=='UL'){ul=document.createElement('ul');box.append(ul)}el=document.createElement('li');appendCites(el,t.replace(/^[-*]\s+/,''));ul.append(el);return}
    else if(/^>\s?/.test(t)){el=document.createElement('blockquote');appendCites(el,t.replace(/^>\s?/,''))}
    else{el=node('p');appendCites(el,t)}
    box.append(el)
  })
}
function appendCites(container,text){String(text||'').split(/(\[E-\d{3}\])/g).forEach(p=>{if(/^\[E-\d{3}\]$/.test(p)){const k=p.slice(1,-1),b=node('button',p,'citation');b.onclick=()=>showEvidence(currentEvidence[k]||{evidence_id:k,quote:'当前证据库未找到该编号'});container.append(b)}else container.append(document.createTextNode(p))})}
async function viewArtifact(id,aid){try{const md=await apiText('/api/jobs/'+encodeURIComponent(id)+'/artifacts/'+encodeURIComponent(aid)+'/content');renderReportMarkdown(md);$('reportMeta').textContent=aid}catch(e){toast(e.message)}}

async function loadProcess(id){
  try{currentProcess=await api('/api/jobs/'+encodeURIComponent(id)+'/process');renderProcess(currentProcess)}
  catch(e){currentProcess={note:e.message};renderProcess(currentProcess)}
}
function modeLabel(m){return {auto:'Auto',single:'单智能体',fixed:'固定研究链',manager_worker:'统筹者-工作者',fanout:'并行分工',dynamic_team:'动态团队',debate:'正反辩论'}[m]||m||'自动'}
function findMode(o){return o?.selected_mode||o?.mode||o?.orchestration||o?.strategy||o?.meta?.mode||o?.plan?.mode||'auto'}
function findReason(o){return o?.reason||o?.selection_reason||o?.meta?.reason||o?.note||'系统根据任务结构、资料、工具、预算和能力选择执行方式。'}
function renderProcess(p){
  const m=findMode(p),reason=findReason(p);$('sumMode').textContent=modeLabel(m);$('sumModeReason').textContent=reason;$('processMode').textContent=modeLabel(m);$('processReason').textContent=reason;$('processRaw').textContent=JSON.stringify(p,null,2);
  const human=$('processHuman');human.replaceChildren();const tasks=p?.tasks||p?.plan?.tasks||p?.subtasks||[];
  if(Array.isArray(tasks)&&tasks.length){const list=node('div',null,'stages-vertical');tasks.forEach((t,i)=>{const r=node('div',null,'stage-row '+(['completed','success','done'].includes(t.status)?'done':t.status==='running'?'running':''));r.append(node('i',String(i+1)));const x=node('div');x.append(node('strong',t.description||t.task||t.id||('子任务'+(i+1))),node('span',[t.role,t.status,t.depends_on&&('依赖 '+[].concat(t.depends_on).join(','))].filter(Boolean).join(' · ')));r.append(x);list.append(r)});human.append(list)}else human.append(empty('暂无结构化子任务记录','原始过程记录仍可在下方查看。'));
  const stages=currentJobProgress?.pipeline?.result?.stages||[];const sh=$('processStages');sh.replaceChildren();if(stages.length)stages.forEach((s,i)=>{const st=String(s.status||''),cls=st.includes('fail')?'fail':st.includes('run')?'running':'done';const r=node('div',null,'stage-row '+cls);r.append(node('i',cls==='done'?'✓':String(i+1)));const x=node('div');x.append(node('strong',s.stage||('阶段'+(i+1))),node('span',s.message||s.status||''));r.append(x);sh.append(r)});else sh.append(empty('暂无阶段记录'))
}
function renderVersions(){const h=$('versionList');if(!h)return;h.replaceChildren();const arts=currentArtifacts.filter(a=>['report','analysis','collection','review','outline','material_pack'].includes(a.kind));if(!arts.length){h.append(empty('暂无版本产物'));return}arts.sort((a,b)=>(b.version||0)-(a.version||0)).forEach(a=>{const r=node('div',null,'version-row'),x=node('div');x.append(node('strong',a.artifact_id),node('span',`${a.kind||'artifact'} · ${a.producer||'—'} · ${fmtTime(a.created_at)}`));const actions=node('div');const v=node('button','查看','btn btn-small');v.onclick=()=>{switchJobTab('result',document.querySelector('[data-jobtab="result"]'));viewArtifact(currentJob,a.artifact_id)};actions.append(v);r.append(x,actions);h.append(r)})}
function switchJobTab(name,btn){document.querySelectorAll('[data-jobtab]').forEach(x=>x.classList.toggle('active',x.dataset.jobtab===name));document.querySelectorAll('.tab-panel').forEach(x=>x.classList.remove('active'));$('jobtab-'+name).classList.add('active');if(name==='process'&&currentProcess)renderProcess(currentProcess)}
async function cancelJob(){if(!currentJob)return;try{const d=await api('/api/jobs/'+encodeURIComponent(currentJob)+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});toast(d.status==='stopped'?'任务已停止':'已请求停止')}catch(e){toast(e.message)}}
async function resumeJob(){if(!currentJob)return;try{await api('/api/jobs/'+encodeURIComponent(currentJob)+'/resume',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});toast('任务已进入恢复流程');reopenCurrentJob()}catch(e){toast(e.message)}}
async function reviseJob(){const instruction=$('revinstr').value.trim();if(!instruction){toast('请填写改稿要求');return}try{const d=await api('/api/jobs/'+encodeURIComponent(currentJob)+'/revise',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction})});$('revinstr').value='';toast('已创建改稿任务');await loadJobs();go('/job/'+d.job_id)}catch(e){toast(e.message)}}
function exportReport(fmt){const reports=currentArtifacts.filter(a=>['report','analysis','collection'].includes(a.kind)).sort((a,b)=>(a.version||0)-(b.version||0));if(!reports.length){toast('没有可下载的交付产物');return}if(DEMO){toast('预览模式不下载文件');return}const a=reports[reports.length-1];location.href='/api/jobs/'+encodeURIComponent(currentJob)+'/export?format='+encodeURIComponent(fmt||'md')+'&artifact_id='+encodeURIComponent(a.artifact_id)}
function exportHtml(){if(!currentJob)return false;if(DEMO){toast('预览模式不下载文件');return false}location.href='/api/jobs/'+encodeURIComponent(currentJob)+'/export.html';return false}
function openSourceLink(url){if(DEMO){toast('预览模式不打开外链');return false}window.open(url,'_blank','noopener,noreferrer');return false}
function sourceUrl(s){try{const u=String(s.final_url||s.original_address||'');if(!/^https?:\/\//i.test(u))return '';const h=new URL(u).hostname;if(/^(localhost|127\.0\.0\.1|\[::1\]|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/i.test(h))return '';return u}catch(e){return ''}}

async function loadRuns(){
  const host=$('runs');if(!host)return;
  try{const d=await api('/api/runs'),runs=d.runs||[];host.replaceChildren();if(!runs.length){host.append(empty('暂无Run记录','通用Agent或底层运行会出现在这里。'));return}
    runs.slice(0,50).forEach(r=>{const e=node('div',null,'run-item');e.onclick=()=>showRun(r.run_id,e);e.append(node('strong',short(r.run_id,28)),node('span',`${statusInfo(r.status)[0]} · ${r.mode||'—'} · ${r.model||'—'}`));host.append(e)})
  }catch(e){host.replaceChildren(empty('加载失败',e.message))}
}
async function showRun(id,el){
  currentRun=id;runPollToken++;const token=runPollToken;document.querySelectorAll('.run-item').forEach(x=>x.classList.remove('active'));el?.classList.add('active');$('runTitle').textContent=id;$('stream').textContent='';pollRun(id,token)
}
async function pollRun(id,token){
  let after=0,url='/api/runs/'+encodeURIComponent(id);
  while(token===runPollToken&&id===currentRun){
    try{
      const [ev,meta,hit,tr,tools,job,plan]=await Promise.all([api(url+'/events?after='+after),api(url+'/meta'),api(url+'/hitl'),api(url+'/trace'),api(url+'/tools'),api(url+'/job'),api(url+'/plan')]);if(token!==runPollToken)return;
      after=ev.after;(ev.new||[]).forEach(x=>$('stream').textContent+='['+(x.type||'event')+'] '+JSON.stringify(x).slice(0,260)+'\n');
      $('status').replaceChildren(badge(meta.status));$('answer').textContent=meta.final_text||'尚无最终回答';const led=job.ledger;$('jobusage').textContent=led?`调用 ${led.call_count} · 输出 ${led.output_tokens} Token · 估算 ${money(led.estimated_cost_usd)}`:(job.note||'暂无根任务账本');
      renderTrace(tr.events||[]);renderTools(tools.tools||[]);$('plan').textContent=JSON.stringify(plan,null,2);
      pendingRequest=hit.pending;$('decide').disabled=!pendingRequest;$('pending').textContent=pendingRequest?`${pendingRequest.name}\n${JSON.stringify(pendingRequest.arguments,null,2)}`:'当前无待审批调用。';
      if(['completed','failed','cancelled'].includes(meta.status))return;await sleep(700)
    }catch(e){toast(e.message);return}
  }
}
function renderTrace(events){const h=$('trace');h.replaceChildren();if(!events.length){h.append(empty('暂无Trace'));return}events.slice(-120).forEach(e=>{const type=String(e.type||''),cls=type.includes('tool')?'tool':type.includes('llm')?'llm':type.includes('run')?'run':'';const r=node('div',null,'trace '+cls);r.append(node('i'));const x=node('div');x.append(node('strong',type||'event'),node('span',[e.node,fmtTime(e.timestamp)].filter(Boolean).join(' · ')));const detail={...e};delete detail.type;delete detail.node;delete detail.timestamp;delete detail.run_id;const c=node('code',JSON.stringify(detail).slice(0,420));x.append(c);r.append(x);h.append(r)})}
function renderTools(items){const h=$('tools');h.replaceChildren();if(!items.length){h.append(empty('暂无Tool调用'));return}items.forEach(t=>{const c=node('div',null,'tool-card');c.append(node('strong',t.name||'tool'),node('code',(t.result||'').slice(0,700)));h.append(c)})}
function switchObserveTab(name,btn){document.querySelectorAll('[data-obtab]').forEach(x=>x.classList.toggle('active',x.dataset.obtab===name));['answer','trace','tools','plan','stream','hitl'].forEach(x=>$('ob-'+x).classList.toggle('hidden',x!==name))}
async function hitl(){if(!currentRun||!pendingRequest)return;try{await api('/api/hitl/decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:currentRun,request_id:pendingRequest.request_id,action:$('act').value})});$('hitlmsg').textContent='已提交';$('decide').disabled=true;toast('审批决策已提交')}catch(e){toast(e.message)}}

async function loadEval(){const mode=$('mode').value;try{const d=await api('/api/eval?mode='+encodeURIComponent(mode)),h=$('eval');h.replaceChildren();if(d.note)h.append(node('div',d.note,'notice'));if(d.meta)h.append(kv('模式',d.meta.mode||mode));if(d.totals){const grid=node('div',null,'kv-grid');Object.entries(d.totals).slice(0,8).forEach(([k,v])=>grid.append(kv(k,typeof v==='object'?JSON.stringify(v):v)));h.append(grid)}}catch(e){$('eval').textContent=e.message}}
function renderStats(){const h=$('statsBody');if(!h)return;const active=jobsCache.filter(j=>isActive(j.status)).length,done=jobsCache.filter(j=>['completed','partial'].includes(j.status)).length,attention=jobsCache.filter(j=>isAttention(j.status)).length;h.replaceChildren(kv('任务总数',jobsCache.length),kv('进行中',active),kv('已交付',done),kv('需要处理',attention))}
function refreshCurrent(){const p=(location.hash||'#/home').slice(1);if(p.startsWith('/job/')){reopenCurrentJob()}else if(p==='/tasks'||p==='/home')loadJobs();else if(p==='/observe')loadRuns();else{loadConfig();loadJobs();loadEval()}}
function loadLibraryCompat(job){return loadLibrary(job)}
/* 兼容既有测试与文档名称 */
window.loadLibrary=loadLibrary;
window.loadJobs=loadJobs;
window.renderReportMarkdown=renderReportMarkdown;

loadConfig();loadJobs();route();
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
