"""根任务模型调用网关。先写调用意图再请求；账本不保存消息、密钥或响应正文。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path
import json
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
        # D1-02：目录可能由编排器"调度前预留"（只含 orchestration.json），
        # 防重复执行的检查在应用层 run() 入口（含执行产物即拒绝），这里允许复用。
        directory.mkdir(parents=True, exist_ok=True)
        self.request = request
        self.clock = clock
        self.started = clock()
        # B2在同一根任务内串行发起模型请求，避免费用/Token预留竞争。
        self.lock = threading.RLock()
        # D2-01：同一 job 目录的多阶段账本续接——调度、链、工具阶段的调用
        # 共存于一份根 ledger.json，逐笔可对应（不存在各自覆盖）。
        self.calls = []
        # D2-02：在途预算预留（发请求前冻结，完成后结算/释放；未知结果保守保留）。
        self.reservations = []
        prior = directory / "ledger.json"
        if prior.exists():
            try:
                data = json.loads(prior.read_text(encoding="utf-8"))
                self.calls = list(data.get("calls", []))
                self.reservations = list(data.get("reservations", []))
            except ValueError:
                self.calls = []   # 损坏的历史账本不猜测，从头记录并保留告警位置
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

    def _model_calls(self):
        """参与调用数/Token/未知统计的条目（child_run 汇总与 search 记账不计调用次数）。"""
        return [c for c in self.calls if c.get("kind") not in ("child_run", "search")]

    def summary(self):
        model_calls = self._model_calls()
        known = sum(c.get("estimated_cost_usd") or 0 for c in self.calls)
        uncertain = sum(c.get("usage_complete") is not True or c.get("estimated_cost_usd") is None
                        for c in model_calls)
        return {"schema_version": 1, "root_job_id": self.job_id, "status": self.status,
                "stop_reason": self.stop_reason, "mode": self.request.mode,
                "run_ids": list(self.run_ids), "call_count": len(model_calls),
                "prompt_tokens": sum(c.get("prompt_tokens") or 0 for c in model_calls),
                "output_tokens": sum(c.get("completion_tokens") or 0 for c in model_calls),
                "known_estimated_cost_usd": known,
                "reserved_usd": self._reserved_total(),
                "reservations": self.reservations,
                "estimated_cost_usd": None if uncertain else known,
                "unknown_usage_calls": uncertain, "price_version": PRICE_VERSION,
                "elapsed_seconds": round(self.clock() - self.started, 4),
                "limits": {"max_calls": self.request.max_calls,
                           "max_output_tokens": self.request.max_output_tokens,
                           "max_seconds": self.request.max_seconds, "max_cost": self.max_cost},
                "calls": self.calls}

    def record_search(self, *, provider, query, urls=None, elapsed_seconds=0.0,
                      cost_usd=None, error=""):
        """D3-01：一次搜索调用的记账条目（kind=search；不计模型调用次数）。"""
        with self.lock:
            self._reload()
            self.calls.append({
                "call_id": uuid.uuid4().hex, "root_job_id": self.job_id,
                "kind": "search", "purpose": "search", "provider": provider,
                "query": query, "urls": list(urls or []), "error": error[:200],
                "elapsed_seconds": round(elapsed_seconds, 3),
                "estimated_cost_usd": cost_usd,
                "status": "failed" if error else "completed",
                "usage_complete": True, "prompt_tokens": 0, "completion_tokens": 0})
            self.write()

    def record_child_run(self, *, child_job_id, role, draft_level=None,
                         cost_usd=None, attempts=1):
        """D2-01：子任务运行的成本汇总入根账本（不计模型调用次数，费用计入根预算）。"""
        with self.lock:
            self.calls.append({
                "call_id": uuid.uuid4().hex, "root_job_id": self.job_id,
                "child_job_id": child_job_id, "kind": "child_run",
                "purpose": "child_run", "role": role or "child",
                "status": "completed", "usage_complete": True,
                "draft_level": draft_level, "attempts": attempts,
                "estimated_cost_usd": cost_usd,
                "prompt_tokens": 0, "completion_tokens": 0})
            self.write()

    # ---- D2-02：原子预留 / 结算 / 释放 --------------------------------------
    def _reload(self):
        """合并其他账本句柄对同一文件的变动（写前重读；文件原子替换保证不撕裂）。"""
        prior = self.directory / "ledger.json"
        if prior.exists():
            try:
                data = json.loads(prior.read_text(encoding="utf-8"))
                self.calls = list(data.get("calls", []))
                self.reservations = list(data.get("reservations", []))
            except ValueError:
                pass

    def _reserved_total(self):
        return sum(r["cost_usd"] for r in self.reservations
                   if r["status"] == "reserved")

    def reserve(self, cost_usd, *, purpose, ref_id=""):
        """发请求前原子冻结预算；余量不足立即 BudgetStop（两个并发申请不能超分）。"""
        with self.lock:
            self._reload()
            active = self._reserved_total()
            known = sum(c.get("estimated_cost_usd") or 0 for c in self.calls)
            if self.max_cost is not None and (
                    self.max_cost <= 0
                    or known + active + cost_usd > self.max_cost + 1e-9):
                # 零预算没有任何可预留额度：零预算零请求
                self.stop_reason = self.stop_reason or "cost_limit"
                self.write()
                raise BudgetStop(
                    f"根任务预算预留失败：已用 {known:.4f} + 在途 {active:.4f}，"
                    f"需预留 {cost_usd:.4f}，上限 {self.max_cost}")
            rid = uuid.uuid4().hex
            self.reservations.append({"id": rid, "cost_usd": cost_usd,
                                      "purpose": purpose, "ref_id": ref_id,
                                      "status": "reserved"})
            self.write()
            return rid

    def settle(self, reservation_id, actual_cost_usd, *, child_job_id="",
               role="child", draft_level=None, attempts=1):
        """完成后结算：实际费用入账；未知（None）按预留额保守入账并标记不完整。

        失败且确认未花费时以 actual_cost_usd=0 结算；不能把预留"重置"回可用额度
        之后又当作未发生——一切以入账条目为准。
        """
        with self.lock:
            self._reload()
            reserved_amount = None
            for r in self.reservations:
                if r["id"] == reservation_id and r["status"] == "reserved":
                    r["status"] = ("settled" if actual_cost_usd is not None
                                   else "settled_unknown")
                    r["actual_cost_usd"] = actual_cost_usd
                    reserved_amount = r["cost_usd"]
                    break
            if reserved_amount is None:
                return
            entry_cost = (actual_cost_usd if actual_cost_usd is not None
                          else reserved_amount)
            self.calls.append({
                "call_id": uuid.uuid4().hex, "root_job_id": self.job_id,
                "child_job_id": child_job_id, "kind": "child_run",
                "purpose": "child_run", "role": role,
                "status": "completed", "usage_complete": actual_cost_usd is not None,
                "conservative": actual_cost_usd is None,
                "draft_level": draft_level, "attempts": attempts,
                "estimated_cost_usd": entry_cost,
                "prompt_tokens": 0, "completion_tokens": 0})
            self.write()

    def release(self, reservation_id):
        """释放预留（确认未发出请求时使用）；已结算/未知的不允许释放。"""
        with self.lock:
            self._reload()
            for r in self.reservations:
                if r["id"] == reservation_id and r["status"] == "reserved":
                    r["status"] = "released"
                    break
            self.write()

    def write(self):
        write_json(self.directory / "ledger.json", self.summary())

    def check(self, llm=None, *, new_call=True):
        s = self.summary()
        model_calls = self._model_calls()
        reason = self.stop_reason
        if self.closed:
            reason = reason or "job_closed"
        elif self.clock() - self.started >= self.request.max_seconds:
            reason = reason or "time_limit"
        elif new_call and len(model_calls) >= self.request.max_calls:
            reason = reason or "call_limit"
        elif s["output_tokens"] >= self.request.max_output_tokens:
            reason = reason or "output_token_limit"
        elif self.max_cost is not None and (s["known_estimated_cost_usd"]
                                            + self._reserved_total()) >= self.max_cost:
            reason = reason or "cost_limit"
        elif any(c.get("usage_complete") is not True for c in model_calls):
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


def _assemble_messages(messages: list[dict]) -> tuple[list[dict], dict]:
    """D4-01：非工具续轮的模型调用统一过 Context Builder。

    AgentRuntime 已完成组装的消息带 <<CONTEXT>> 标记或包含 tool 配对时原样跳过；
    研究链等普通 system/user 提示词会原样重组并经过统一的限窗/修剪逻辑。
    """
    if not messages:
        return messages, {}
    if any(m.get("role") == "tool" or m.get("tool_calls") for m in messages):
        return messages, {"already_assembled": 1}
    if any("<<CONTEXT>>" in str(m.get("content") or "") for m in messages):
        return messages, {"already_assembled": 1}
    last_user = -1
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            last_user = index
            break
    if last_user < 0:
        return messages, {}
    system_parts = [str(m.get("content") or "") for m in messages[:last_user]
                    if m.get("role") == "system"]
    history = [m for m in messages[:last_user] if m.get("role") != "system"]
    from src.harness.context.builder import compose_context
    assembled, stats = compose_context(
        str(messages[last_user].get("content") or ""), [], history=history,
        total_budget=6000, base_system="\n\n".join(system_parts))
    return assembled, {"context_assembled": 1, **stats}


def model_call(llm, messages, tools=None, *, purpose="agent", role=None):
    assembled, _ = _assemble_messages(messages)
    ledger = ACTIVE_JOB.get()
    if ledger is None:
        return llm.chat(assembled, tools=tools)
    return ledger.call(llm, assembled, tools, purpose, role)
