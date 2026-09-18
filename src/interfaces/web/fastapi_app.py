# -*- coding: utf-8 -*-
"""
interfaces/web/fastapi_app.py —— FastAPI 适配层（可选依赖）

复用与 CLI / 标准库工作台完全相同的业务契约（TaskRequest + ResearchApplication），
只是把 HTTP 层换成 FastAPI——框架差异只存在于传输层，业务规则零拷贝。

可选依赖说明：FastAPI + uvicorn 不是本项目默认运行依赖（默认入口是零依赖的
`python -m src.interfaces.web.workbench`）。安装后可用：
    pip install fastapi uvicorn
    python -m src.interfaces.web.fastapi_app --port 8766

端点（与标准库工作台同语义的最小集）：
    GET  /                → React 前端页面（static/react/index.html）
    GET  /api/health      → 存活与模式说明
    POST /api/runs        → 提交任务（goal/flow/mode/...），后台线程执行
    GET  /api/runs/{id}   → 状态与结果
    GET  /api/runs        → 任务列表
内存任务表仅为本适配层的演示实现；生产入口与持久化队列仍在标准库工作台。
"""

from __future__ import annotations

import argparse
import threading
import uuid
from pathlib import Path

try:  # 可选依赖：未安装时给出清晰指引而不是 ImportError 堆栈
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse
    from pydantic import BaseModel
except ImportError as _e:  # pragma: no cover
    raise SystemExit("需要可选依赖：pip install fastapi uvicorn") from _e

from src.application.research import ResearchApplication
from src.application.request import TaskRequest

STATIC_DIR = Path(__file__).resolve().parent / "static"
REACT_INDEX = STATIC_DIR / "react" / "index.html"

# 内存任务表：job_id → {"status"/"goal"/"result"/"error"}（演示适配层，不持久化）
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


class RunIn(BaseModel):
    """提交请求体：字段与 TaskRequest 的核心子集同名，语义一致。"""

    goal: str
    flow: str = "agent"            # agent | research
    mode: str = "mock"             # mock | real（缺 Key 会在执行期显式失败）
    max_calls: int = 12
    max_cost: float | None = None


def build_app() -> "FastAPI":
    app = FastAPI(title="agent-mvp 研究工作台（FastAPI 适配层）")

    # React 页的本地运行时（vendor/）。目录不存在时不挂载——缺失会走页面内
    # 的 CDN 兜底与回退提示，不让应用启动失败。
    vendor_dir = REACT_INDEX.parent / "vendor"
    if vendor_dir.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/vendor", StaticFiles(directory=str(vendor_dir)),
                  name="vendor")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        if REACT_INDEX.exists():
            return REACT_INDEX.read_text(encoding="utf-8")
        return ("<!doctype html><meta charset='utf-8'>"
                "<p>React 前端页面缺失（src/interfaces/web/static/react/index.html）。"
                "请改用标准库工作台：python -m src.interfaces.web.workbench</p>")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "adapter": "fastapi",
                "note": "演示适配层；生产入口为 src.interfaces.web.workbench"}

    @app.post("/api/runs", status_code=202)
    def submit(run: RunIn) -> dict:
        job_id = "job_" + uuid.uuid4().hex
        with _JOBS_LOCK:
            _JOBS[job_id] = {"status": "running", "goal": run.goal, "result": "",
                             "error": ""}
        request = TaskRequest(task=run.goal, flow=run.flow, mode=run.mode,
                              max_calls=max(1, run.max_calls),
                              max_cost=run.max_cost)
        worker = threading.Thread(target=_execute, args=(job_id, request),
                                  daemon=True)
        worker.start()
        return {"job_id": job_id, "status": "running"}

    @app.get("/api/runs/{job_id}")
    def detail(job_id: str) -> dict:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {"job_id": job_id, **job}

    @app.get("/api/runs")
    def listing() -> JSONResponse:
        with _JOBS_LOCK:
            items = [{"job_id": jid, **job} for jid, job in _JOBS.items()]
        return JSONResponse({"jobs": items})

    return app


def _execute(job_id: str, request: TaskRequest) -> None:
    """后台线程：走统一业务入口跑完并回填状态（异常也如实落 status=failed）。"""
    application = ResearchApplication(request)
    try:
        outcome = application.run()
        # 统一入口的返回可能是 RunOutcome 对象（agent 流）或 dict（含 job 摘要）
        final_text = getattr(outcome, "final_text", None)
        status = getattr(outcome, "status", None)
        if isinstance(outcome, dict):
            final_text = outcome.get("final_text", final_text)
            status = outcome.get("status", status)
        with _JOBS_LOCK:
            _JOBS[job_id].update(status=str(status or "completed").lower(),
                                 result=str(final_text or "")[:20000])
    except Exception as e:  # noqa: BLE001 —— 演示适配层只回显类型与信息摘要
        with _JOBS_LOCK:
            _JOBS[job_id].update(status="failed", error=f"{type(e).__name__}: {e}"[:500])


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(build_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
