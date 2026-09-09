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
        request = TaskRequest.from_payload(payload)
        if request.flow == "research":
            # S5：研究写作链任务进入队列（worker 领取后执行；进度/取消/恢复可查）
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
        elif content_type.startswith("text/html"):
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
        if len(segments) == 2 and segments[1] == "progress":
            row = self.state.queue.get(job_dir.name) if _JOB_ID.fullmatch(job_dir.name) else None
            return self._send(200, {"job": row, "job_file": _load(job_dir / "job.json"),
                                    "pipeline": _load(job_dir / "pipeline.json")})
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
            return self._send(200, diagnose_config(
                self.state.settings, mode=query.get("mode", ["mock"])[0],
                profile_name=query.get("profile", [None])[0]))
        if url.path == "/api/eval":
            mode = parse_qs(url.query).get("mode", ["mock"])[0]
            if mode not in ("mock", "real"):
                return self._send(400, {"error": "评测模式必须为 mock 或 real"})
            path = REAL_EVAL_REPORT if mode == "real" else EVAL_REPORT
            return self._send(200, _load(path) or {"mode": mode, "note": "尚无该模式的 benchmark 报告"})
        parts = [p for p in url.path.split("/") if p]
        if parts == ["api", "jobs"]:
            return self._send(200, {"jobs": self.state.queue.list(limit=50)})
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


INDEX_HTML = """<!doctype html><html lang="zh"><meta charset="utf-8"><title>Agent Workbench</title>
<style>body{font-family:system-ui;margin:24px;background:#0f1420;color:#dbe4f0}
.card{background:#1a2233;border:1px solid #2c3a55;border-radius:10px;padding:14px;margin:10px 0}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #334;padding:6px;font-size:13px}
pre{white-space:pre-wrap;font-size:12px}.mono{font-family:Consolas,monospace}</style>
<h1>Agent Workbench（纯标准库 MVP）</h1>
<div class="card"><h3>① Run Dashboard</h3><div id="runs"></div></div>
<div class="card"><h3>⑪ Streaming：新任务</h3>
任务：<input id="task" size="50" value="帮我计算 6*7">
<select id="mode" onchange="loadConfig()"><option value="mock">Mock 离线演示</option>
<option value="real">真实模型</option></select>
<select id="flow" onchange="if(this.value==='research')loadConfig()"><option value="agent">通用 Agent（默认）</option>
<option value="research">研究写作链（整理/报告，走队列）</option></select>
<button id="start" onclick="startRun()" disabled>运行</button>
<button onclick="loadConfig()">检查配置</button>
<p id="config"></p>
<div>任务限制：
<label>模型调用数 <input id="maxcalls" type="number" min="0" step="1" value="12" size="5"></label>
<label>输出Token <input id="maxtokens" type="number" min="0" step="1" value="8192" size="6"></label>
<label>运行秒数 <input id="maxseconds" type="number" min="0" value="300" size="5"></label>
<label>估算美元阈值 <input id="maxcost" type="number" min="0" step="0.01" value="0.05" size="5"></label>
</div>
<p>费用为本地参考估算，最后一笔可能越过阈值；时间限制在调用边界检查，不能强制终止后台工具。</p>
<p>真实模式会将任务文本和工具结果发送到项目配置的模型服务。当前内置工具仅支持计算和时间；
B3/B4 已接入本地资料与用户指定网页链接导入（默认安全策略拒绝私网/回环地址），研究成稿链在 B5 接入。</p>
<div>本地资料（可选）：
文件路径（每行一个，TXT/Markdown，只读）：<textarea id="filepaths" rows="2" cols="60" placeholder="D:/资料/笔记.md"></textarea><br>
网页链接（每行一个 http(s)，抓取正文并登记来源）：<textarea id="urls" rows="2" cols="60" placeholder="https://example.com/article"></textarea><br>
粘贴文本：<textarea id="pastetext" rows="3" cols="60" placeholder="整段作为一份资料来源"></textarea></div>
<div>当前 run：<span id="cur"></span></div>
<div id="status"></div><pre id="answer"></pre>
<pre id="jobusage"></pre>
<div id="stream" class="mono"></div></div>
<div class="card"><h3>④ Trace Viewer / ③ Timeline</h3><div id="trace"></div></div>
<div class="card"><h3>⑯ Tool Cards</h3><div id="tools"></div></div>
<div class="card"><h3>⑱ HITL Panel</h3><pre id="pending"></pre>
<select id="act"><option>approve</option><option>reject</option></select>
<button id="decide" onclick="hitl()" disabled>提交决策</button><span id="hitlmsg"></span></div>
<div class="card"><h3>⑳ 资料与产物（B3/B4/B5：来源、证据与报告产物查看）</h3>
<div id="libnote">研究任务运行后，这里显示来源解析状态、证据引用与报告产物；点击行查看内容。</div>
<div id="srclist"></div><div id="artlist"></div><pre id="docview"></pre></div>
<div class="card"><h3>㉑ 研究任务（S5：队列/进度/停止/恢复/导出）</h3>
<div id="jobnote">提交研究写作链任务后在此跟踪：排队→阶段进度→结果分级；可停止（分阶段收敛）或对中断任务恢复。</div>
<div id="jobtbl"></div>
<div>当前 job：<span id="curjob"></span><span id="jobstate"></span>
<button id="btncancel" onclick="cancelJob()" disabled>停止</button>
<button id="btnresume" onclick="resumeJob()" disabled>恢复</button>
<button id="btnexport" onclick="exportReport()" disabled>导出 Markdown</button>
改稿指令：<input id="revinstr" size="28" placeholder="如：缩短到150字并保留引用">
<button id="btnrevise" onclick="reviseJob()" disabled>追问改稿</button></div>
<pre id="joblog"></pre>
<div id="reportview"></div>
<div id="rtok"></div>
<pre id="evidenceview"></pre></div>
<div class="card"><h3>⑨ Eval Dashboard</h3><div id="eval"></div></div>
<script>
const $=id=>document.getElementById(id);
let viewVersion=0, configVersion=0, pendingRequest=null, jobShown='';
function textElement(tag,value){const el=document.createElement(tag);el.textContent=value??'';return el}
async function j(url,opt){const r=await fetch(url,opt);const d=await r.json();
 if(!r.ok)throw new Error(d.error||'请求失败');return d}
async function loadRuns(){const d=await j('/api/runs');
 const table=document.createElement('table'),head=document.createElement('tr');
 ['run_id','状态','运行模式','模型','trace','操作'].forEach(x=>head.append(textElement('th',x)));table.append(head);
 d.runs.slice(0,10).forEach(r=>{const row=document.createElement('tr');
  [r.run_id,r.status,r.mode||'历史记录：未记录',r.model,r.trace_count].forEach(x=>row.append(textElement('td',x)));
  const cell=document.createElement('td'),button=textElement('button','查看');
  button.addEventListener('click',()=>showRun(r.run_id));cell.append(button);row.append(cell);table.append(row)});
 $('runs').replaceChildren(table) }
async function showRun(id){const version=++viewVersion;$('cur').textContent=id;
 pendingRequest=null;$('decide').disabled=true;$('hitlmsg').textContent='';
 $('answer').textContent='';$('stream').textContent='';$('jobusage').textContent='';
 jobShown='';$('srclist').replaceChildren();$('artlist').replaceChildren();
 $('docview').textContent='';$('libnote').textContent='任务带资料运行后显示来源与产物。';
 try{await poll(id,version)}catch(e){if(version===viewVersion)$('status').textContent=e.message} }
async function loadConfig(){const version=++configVersion,mode=$('mode').value;$('start').disabled=true;
 loadEval().catch(e=>$('eval').textContent=e.message);
 $('config').textContent='正在检查配置…';
 try{const d=await j('/api/config?mode='+encodeURIComponent(mode));if(version!==configVersion)return;
  $('config').textContent=d.ready?`${d.mode} / ${d.provider} / ${d.model}。仅检查本地配置，未验证连接、认证或模型能力。`:d.errors.join('；');
  $('start').disabled=!d.ready;
 }catch(e){if(version===configVersion)$('config').textContent=e.message}}
async function startRun(){const t=$('task').value;
 try{
 const files=$('filepaths').value.split(/\\r?\\n/).map(x=>x.trim()).filter(Boolean);
 const urls=$('urls').value.split(/\\r?\\n/).map(x=>x.trim()).filter(Boolean);
 const texts=$('pastetext').value.trim()?[$('pastetext').value]:[];
 const r=await j('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({task:t,mode:$('mode').value,flow:$('flow').value,max_iterations:6,
  texts:texts,files:files,urls:urls,
  max_calls:Number($('maxcalls').value),max_output_tokens:Number($('maxtokens').value),
  max_seconds:Number($('maxseconds').value),max_cost:Number($('maxcost').value)})});
 if(r.job_id){loadJobs().catch(e=>$('status').textContent=e.message);showJob(r.job_id)}
 else if(r.run_id){loadRuns().catch(e=>$('status').textContent=e.message);showRun(r.run_id)}
 }catch(e){$('status').textContent=e.message}}
async function poll(id,version){let after=0;const box=$('stream'),url=`/api/runs/${encodeURIComponent(id)}`;
 while(version===viewVersion){const [d,meta,h,t,tc,job]=await Promise.all([
  j(`${url}/events?after=${after}`),j(`${url}/meta`),j(`${url}/hitl`),j(`${url}/trace`),j(`${url}/tools`),j(`${url}/job`)]);
  if(version!==viewVersion)return;
  after=d.after;d.new.forEach(e=>{box.textContent+='['+e.type+'] '+JSON.stringify(e).slice(0,180)+'\\n'});
  $('status').textContent=[meta.status,meta.termination_reason,meta.error].filter(Boolean).join(' / ');
  $('answer').textContent=meta.final_text||'';
  const usage=job.ledger;
  $('jobusage').textContent=usage?`任务：${usage.root_job_id} / ${usage.status}。模型调用 ${usage.call_count} 次，输出 ${usage.output_tokens} Token，估算费用 ${usage.estimated_cost_usd??'未知'} 美元，未知用量 ${usage.unknown_usage_calls} 次。${usage.stop_reason||''}`:job.note||'正在创建任务账本';
  if(usage&&usage.root_job_id&&jobShown!==usage.root_job_id){loadLibrary(usage.root_job_id).catch(e=>$('libnote').textContent=e.message)}
  $('trace').replaceChildren(...t.events.map(e=>textElement('pre',JSON.stringify(e))));
  $('tools').replaceChildren(...tc.tools.map(x=>textElement('div',`${x.name}: ${x.result}`)));
  pendingRequest=h.pending;$('decide').disabled=!pendingRequest;
  $('pending').textContent=pendingRequest?`${pendingRequest.name}\\n${JSON.stringify(pendingRequest.arguments,null,2)}`:'当前无待审批调用';
  if(['completed','failed','cancelled'].includes(meta.status) && (!usage||usage.status!=='running')){await loadRuns();return}
  await new Promise(r=>setTimeout(r,500))}}
async function hitl(){const run=$('cur').textContent;if(!run)return;
 if(!pendingRequest)return;const request=pendingRequest;$('decide').disabled=true;
 try{
 await j('/api/hitl/decide',{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({run_id:run,request_id:request.request_id,action:$('act').value})});$('hitlmsg').textContent='决策已提交';pendingRequest=null;
 }catch(e){$('hitlmsg').textContent=e.message}}
async function showDoc(url){try{const r=await fetch(url);$('docview').textContent=r.ok?await r.text():('HTTP '+r.status+'：无法读取')}catch(e){$('docview').textContent=e.message}}
function rowOf(cells){const row=document.createElement('tr');
 cells.forEach(x=>row.append(textElement('td',String(x??''))));return row}
async function loadLibrary(job){jobShown=job;$('docview').textContent='';
 const s=await j('/api/jobs/'+encodeURIComponent(job)+'/sources');
 $('libnote').textContent=`任务 ${job}：共 ${s.total} 个来源，可用 ${s.usable}（${JSON.stringify(s.statuses||{})}）。点击来源行查看全文，点击产物行查看内容。`;
 const st=document.createElement('table'),sh=document.createElement('tr');
 ['状态','名称','标题','字节','说明'].forEach(x=>sh.append(textElement('th',x)));st.append(sh);
 (s.sources||[]).forEach(src=>{const row=rowOf([src.status,src.display,src.title||'',src.byte_size,src.status_message||(src.duplicate_of?'重复于 '+src.duplicate_of:'')]);
  if(src.file_name){row.style.cursor='pointer';row.addEventListener('click',()=>showDoc('/api/jobs/'+encodeURIComponent(job)+'/sources/'+encodeURIComponent(src.source_id)+'/text'))}
  st.append(row)});
 $('srclist').replaceChildren(st);
 const a=await j('/api/jobs/'+encodeURIComponent(job)+'/artifacts');
 const at=document.createElement('table'),ah=document.createElement('tr');
 ['artifact_id','版本','父版本','字节','生产者','时间'].forEach(x=>ah.append(textElement('th',x)));at.append(ah);
 (a.artifacts||[]).forEach(art=>{const row=rowOf([art.artifact_id,art.version,art.parent_version??'-',art.byte_size,art.producer,art.created_at||'']);
  row.style.cursor='pointer';row.addEventListener('click',()=>showDoc('/api/jobs/'+encodeURIComponent(job)+'/artifacts/'+encodeURIComponent(art.artifact_id)+'/content'));
  at.append(row)});
 $('artlist').replaceChildren(at)}
let jobVersion=0, jobEvMap={}, curJobId='';
async function loadJobs(){try{const d=await j('/api/jobs');
 const table=document.createElement('table'),head=document.createElement('tr');
 ['job_id','状态/阶段','类型','消息'].forEach(x=>head.append(textElement('th',x)));table.append(head);
 (d.jobs||[]).slice(0,12).forEach(r=>{const row=document.createElement('tr');
  [r.job_id,r.status+' '+(r.stage||''),r.kind,r.message||r.error||''].forEach(x=>row.append(textElement('td',String(x??''))));
  const cell=document.createElement('td'),b=textElement('button','查看');
  b.addEventListener('click',()=>showJob(r.job_id));cell.append(b);row.append(cell);table.append(row)});
 $('jobtbl').replaceChildren(table)}catch(e){$('jobnote').textContent=e.message}}
async function showJob(id){curJobId=id;$('curjob').textContent=id;$('jobstate').textContent='';
 $('joblog').textContent='';$('reportview').replaceChildren();$('rtok').replaceChildren();
 $('evidenceview').textContent='';$('docview').textContent='';$('evidenceview').replaceChildren();jobEvMap={};
 $('srclist').replaceChildren();$('artlist').replaceChildren();$('libnote').textContent='来源/证据/产物（job '+id+'）：';
 loadLibrary(id).catch(()=>{});await pollJob(id)}
async function pollJob(id){while(true){
 try{const d=await j('/api/jobs/'+encodeURIComponent(id)+'/progress');const row=d.job||{};const pl=(d.pipeline||{}).result;const jf=d.job_file||{};
  $('jobstate').textContent=row.status?` 状态:${row.status} 阶段:${row.stage||''} 取消请求:${row.cancel_requested?'是':'否'}`:'';
  const stages=(pl&&pl.stages||[]).map(s=>`[${s.stage}] ${s.status}${s.message?': '+s.message:''}`).join('\n');
  $('joblog').textContent=(pl?`分级:${pl.draft_level} 修订:${pl.revised_rounds||0} 引用:${pl.total_citations||0} 未解析:${pl.unresolved_citations||0}\n`:'')+stages+(jf.error?`\n错误类型:${jf.error}`:'')+(jf.message?`\n${jf.message}`:'');
  if(pl&&pl.draft_level){renderJobResults(id,pl);$('btnresume').disabled=true;$('btncancel').disabled=true;$('btnexport').disabled=false;return}
  const active=['running','queued','waiting_human','cancel_requested','interrupted'].includes(row.status);
  $('btncancel').disabled=!active;
  $('btnresume').disabled=!['failed','partial','cancelled','interrupted'].includes(row.status);
  $('btnexport').disabled=true;
  await new Promise(r=>setTimeout(r,600));
 }catch(e){$('jobstate').textContent=e.message;await new Promise(r=>setTimeout(r,1200))}}}
async function renderJobResults(id,pl){
 const a=await j('/api/jobs/'+encodeURIComponent(id)+'/artifacts');
 const reports=(a.artifacts||[]).filter(x=>x.kind==='report');
 $('btnrevise').disabled=reports.length===0;
 const ev=((await j('/api/jobs/'+encodeURIComponent(id)+'/evidence')).evidence)||[];
 ev.forEach(x=>jobEvMap[x.evidence_id]=x);
 const box=document.createElement('div');
 box.append(textElement('b','报告版本（旧稿不覆盖）： '));
 reports.forEach(art=>{const b=textElement('button',`${art.artifact_id}`);
  b.addEventListener('click',()=>viewArtifact(id,art.artifact_id));box.append(b)});
 box.append(textElement('span','   '));
 if(reports.length){const dl=textElement('a','下载最新 Markdown');
  dl.href='/api/jobs/'+encodeURIComponent(id)+'/artifacts/'+encodeURIComponent(reports[reports.length-1].artifact_id)+'/download';
  box.append(dl)}
 $('reportview').replaceChildren(box);
 const el=document.createElement('div');el.append(textElement('b','证据清单（点击查看原文摘录与定位）：'));
 ev.slice(0,60).forEach(x=>{const b=textElement('button',`${x.evidence_id} ${(x.fact||'').slice(0,28)}`);
  b.addEventListener('click',()=>showEvidence(x));el.append(b)});
 $('evidenceview').replaceChildren(el);
 if(reports.length)viewArtifact(id,reports[reports.length-1].artifact_id)}
async function viewArtifact(id,aid){try{const d=await j('/api/jobs/'+encodeURIComponent(id)+'/artifacts/'+encodeURIComponent(aid)+'/content');renderReportMarkdown(d)}catch(e){$('jobstate').textContent=e.message}}
function renderReportMarkdown(md){const box=document.createElement('div');box.style.whiteSpace='pre-wrap';
 md.split(/(\\[E-\\d{3}\\])/g).forEach(part=>{if(/^\\[E-\\d{3}\\]$/.test(part)){const key=part.slice(1,-1);const b=textElement('button',part);
  b.addEventListener('click',()=>showEvidence(jobEvMap[key]||{evidence_id:key,quote:'该证据不在本任务证据库',fact:'?',tag:'?'}));box.append(b)}
  else box.append(textElement('span',part))});
 $('rtok').replaceChildren(box)}
function showEvidence(x){$('docview').textContent=`${x.evidence_id||''}\n事实：${x.fact||''}\n标注：${x.tag||''}\n摘录：${x.quote||''}\n定位：${JSON.stringify(x.locator||{})}\n来源：${x.source_id||''}\n说明：${x.note||''}`}
async function cancelJob(){if(!curJobId)return;try{const d=await j('/api/jobs/'+encodeURIComponent(curJobId)+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});$('jobstate').textContent=d.status==='stopped'?'已停止（排队中直接取消）':'已请求停止（分阶段收敛中）'}catch(e){$('jobstate').textContent=e.message}}
async function resumeJob(){if(!curJobId)return;try{const d=await j('/api/jobs/'+encodeURIComponent(curJobId)+'/resume',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});$('jobstate').textContent='恢复已排队：'+d.status;await pollJob(curJobId)}catch(e){$('jobstate').textContent=e.message}}
async function reviseJob(){if(!curJobId)return;const instruction=$('revinstr').value.trim();
 if(!instruction){$('jobstate').textContent='请先填写改稿指令';return}
 try{const d=await j('/api/jobs/'+encodeURIComponent(curJobId)+'/revise',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction:instruction})});
  $('jobstate').textContent='改稿任务已排队：'+d.job_id+'（原稿=最新报告，旧版不覆盖）';$('revinstr').value='';
  loadJobs().catch(()=>{});await showJob(d.job_id)}catch(e){$('jobstate').textContent=e.message}}
async function loadEval(){const mode=$('mode').value,d=await j('/api/eval?mode='+mode); if(mode!==$('mode').value)return;
 $('eval').textContent=`评测模式：${d.meta?.mode||d.mode||'历史报告未标记'}；模型：${d.meta?.brain||'未记录'}。运行器冒烟测试，不代表研究写作业务验收。 `+JSON.stringify(d.totals||d)}
loadRuns().catch(e=>$('status').textContent=e.message);
loadJobs().catch(e=>$('jobnote').textContent=e.message);
loadConfig();
</script></html>"""


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
