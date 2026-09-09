"""根任务模型调用网关。先写调用意图再请求；账本不保存消息、密钥或响应正文。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path
import threading
import time
import uuid

from src.harness.run_store import write_json
from src.harness.usage import estimate_cost_usd

ACTIVE_JOB = ContextVar("active_job", default=None)
RUN_ID = ContextVar("model_run_id", default=None)
ROLE = ContextVar("model_role", default="agent")
PRICE_VERSION = "legacy-estimates-b2-v1-unverified"


class BudgetStop(RuntimeError):
    """不能被规划/摘要的普通失败兜底吞掉。"""


@contextmanager
def job_scope(ledger):
    token = ACTIVE_JOB.set(ledger)
    try:
        yield ledger
    finally:
        ACTIVE_JOB.reset(token)


@contextmanager
def role_scope(role):
    token = ROLE.set(role)
    try:
        yield
    finally:
        ROLE.reset(token)


class JobLedger:
    def __init__(self, directory: Path, request, *, clock=time.monotonic):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=False)
        self.request = request
        self.clock = clock
        self.started = clock()
        # B2在同一根任务内串行发起模型请求，避免费用/Token预留竞争。
        self.lock = threading.RLock()
        self.calls = []
        self.run_ids = []
        self.closed = False
        self.stop_reason = None
        self.status = "running"
        self.max_cost = request.max_cost if request.max_cost is not None else (0.05 if request.mode == "real" else None)
        snapshot = request.snapshot() if hasattr(request, "snapshot") else asdict(request)
        write_json(directory / "request.json", snapshot | {"effective_max_cost": self.max_cost})
        self.write()

    @property
    def job_id(self):
        return self.directory.name

    def summary(self):
        known = sum(c.get("estimated_cost_usd") or 0 for c in self.calls)
        uncertain = sum(c.get("usage_complete") is not True or c.get("estimated_cost_usd") is None for c in self.calls)
        return {"schema_version": 1, "root_job_id": self.job_id, "status": self.status,
                "stop_reason": self.stop_reason, "mode": self.request.mode,
                "run_ids": list(self.run_ids), "call_count": len(self.calls),
                "prompt_tokens": sum(c.get("prompt_tokens") or 0 for c in self.calls),
                "output_tokens": sum(c.get("completion_tokens") or 0 for c in self.calls),
                "known_estimated_cost_usd": known,
                "estimated_cost_usd": None if uncertain else known,
                "unknown_usage_calls": uncertain, "price_version": PRICE_VERSION,
                "elapsed_seconds": round(self.clock() - self.started, 4),
                "limits": {"max_calls": self.request.max_calls,
                           "max_output_tokens": self.request.max_output_tokens,
                           "max_seconds": self.request.max_seconds, "max_cost": self.max_cost},
                "calls": self.calls}

    def write(self):
        write_json(self.directory / "ledger.json", self.summary())

    def check(self, llm=None, *, new_call=True):
        s = self.summary()
        reason = self.stop_reason
        if self.closed:
            reason = reason or "job_closed"
        elif self.clock() - self.started >= self.request.max_seconds:
            reason = reason or "time_limit"
        elif new_call and len(self.calls) >= self.request.max_calls:
            reason = reason or "call_limit"
        elif s["output_tokens"] >= self.request.max_output_tokens:
            reason = reason or "output_token_limit"
        elif self.max_cost is not None and s["known_estimated_cost_usd"] >= self.max_cost:
            reason = reason or "cost_limit"
        elif any(c.get("usage_complete") is not True for c in self.calls):
            reason = reason or "usage_unknown"
        elif self.max_cost is not None and (s["unknown_usage_calls"] or (llm is not None
                and getattr(llm, "run_mode", None) != "mock"
                and estimate_cost_usd(llm.model_name, 0, 0) is None)):
            reason = reason or "price_unknown"
        if reason:
            self.stop_reason = reason
            self.write()
            raise BudgetStop("根任务已停止：" + reason)

    def register_run(self, run_id):
        with self.lock:
            self.run_ids.append(run_id)
            self.write()

    def call(self, llm, messages, tools, purpose, role):
        with self.lock:
            self.check(llm)
            mock = getattr(llm, "run_mode", None) == "mock"
            entry = {"call_id": uuid.uuid4().hex, "root_job_id": self.job_id,
                     "run_id": RUN_ID.get(), "role": role or ROLE.get(), "purpose": purpose,
                     "provider": getattr(llm, "provider", "unknown"), "model": llm.model_name,
                     "mode": getattr(llm, "run_mode", "custom"), "status": "started",
                     "usage_complete": False, "estimated_cost_usd": None}
            self.calls.append(entry)
            self.write()  # 失败时不发请求；进程中断时保留未确定的调用意图。
            started = self.clock()
            try:
                if hasattr(llm, "chat_limited"):
                    remaining = self.request.max_output_tokens - self.summary()["output_tokens"]
                    reply = llm.chat_limited(messages, tools=tools, max_tokens=remaining,
                        timeout=max(0.001, self.request.max_seconds - (self.clock() - self.started)))
                else:
                    reply = llm.chat(messages, tools=tools)
                usage = reply.usage or {}
                complete = all(isinstance(usage.get(k), int) and not isinstance(usage[k], bool)
                               and usage[k] >= 0 for k in ("prompt_tokens", "completion_tokens"))
                prompt = usage.get("prompt_tokens") if complete else (0 if mock else None)
                output = usage.get("completion_tokens") if complete else (0 if mock else None)
                entry.update(status="completed", usage_complete=complete or mock,
                             prompt_tokens=prompt, completion_tokens=output,
                             estimated_cost_usd=0.0 if mock else estimate_cost_usd(llm.model_name, prompt, output) if complete else None)
                return reply
            except BaseException as e:
                entry.update(status="failed", error_type=type(e).__name__)
                if mock:
                    # Mock 适配器失败没有真实消耗：已知零成本，不污染未知用量账
                    entry.update(usage_complete=True, prompt_tokens=0,
                                 completion_tokens=0, estimated_cost_usd=0.0)
                else:
                    self.stop_reason = "call_failed_usage_unknown"
                raise
            finally:
                entry["elapsed_seconds"] = round(self.clock() - started, 4)
                self.write()

    def finish(self, status):
        with self.lock:
            self.status = status
            self.closed = True
            self.write()


def check_root_budget(*, new_call=False):
    ledger = ACTIVE_JOB.get()
    if ledger:
        with ledger.lock:
            ledger.check(new_call=new_call)


def model_call(llm, messages, tools=None, *, purpose="agent", role=None):
    ledger = ACTIVE_JOB.get()
    if ledger is None:
        return llm.chat(messages, tools=tools)
    return ledger.call(llm, messages, tools, purpose, role)
