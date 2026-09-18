# -*- coding: utf-8 -*-
"""
S8-02 / D1-01 执行器：把执行方案落到执行——fixed 直跑研究链；fanout 拆子任务各自跑
研究子运行，子产出作为来源文本接入根任务的证据/产物体系，再由成稿与审核通道出报告。

D1-01 统一请求契约：入口（CLI/Web/评测）构造的 TaskRequest 是唯一事实来源；
所有子请求一律用 dataclasses.replace 从原始请求派生——文件、profile、Token 上限、
网络策略、硬要求等字段不会在编排路径丢失；显式零预算保留为 0（=禁止模型调用），
不会被悄悄当成不限额。

设计要点（总计划 D1/D2/D5；设计文档 2.B/2.C/2.D）：
- 任何模式都不得绕过交付标准：最终报告一律由研究写作链产出（双层审校+硬约束复验）；
- 子任务失败：原地重试一次 → 仍失败记录失败原因并继续其余子任务（不静默跳过）；
- 预算：可派工预算 = 根剩余 − 在途预留 − 成稿预留；单子任务 ≤ 池 40%，Σ ≤ 池；
- 并行现状：fanout 按依赖波次真实并发；每个子任务先预留根预算，完成后结算。
"""
from __future__ import annotations

import dataclasses
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path

from src.application.orchestration.guards import (
    GuardViolation,
    OrchestrationGuards,
    allocate_budget,
    compute_dispatchable_pool,
    final_reserve_of,
)
from src.application.orchestration.plan_contract import (
    Budget,
    ExecutionPlan,
    PlanValidationError,
)
from src.application.request import TaskRequest
from src.harness.model_gateway import BudgetStop
from src.harness.run_store import write_json

_SUB_MARKER = "[子智能体 {sid}·{role} 产出，任务：{desc}]\n{text}"


def caps_of(request: TaskRequest) -> Budget:
    """请求 → 预算上限：显式值原样保留；未设费用/时限时用默认配置估值（非实测）。"""
    from src.application.orchestration.scheduler import DEFAULT_BUDGET_CAPS

    return Budget(max_calls=request.max_calls,
                  max_cost_usd=(request.max_cost if request.max_cost is not None
                                else DEFAULT_BUDGET_CAPS.max_cost_usd),
                  max_seconds=(request.max_seconds if request.max_seconds > 0
                               else DEFAULT_BUDGET_CAPS.max_seconds))


class OrchestrationExecutor:
    """按方案派工；app_factory 可注入以便离线测试（默认为研究写作链应用）。"""

    def __init__(self, *, workspace_root, settings=None, llm=None, app_factory=None,
                 state_queue=None):
        self.workspace_root = workspace_root
        self.settings = settings
        self.llm = llm
        self._app_factory = app_factory or self._default_app_factory
        self.state_queue = state_queue

    # ---- 应用装配 -----------------------------------------------------------
    @staticmethod
    def _default_app_factory(workspace_root, settings, llm):
        from src.application.research import ResearchApplication

        def build(request):
            return ResearchApplication(request, settings=settings,
                                       workspace_root=workspace_root, llm=llm)
        return build

    def _run_request(self, request, *, job_id=None, parent_job_id=None) -> object:
        app = self._app_factory(self.workspace_root, self.settings, self.llm)(request)
        return app.run(job_id=job_id, parent_job_id=parent_job_id)

    @staticmethod
    def _derive(request: TaskRequest, *, task: str, budget: Budget | None = None,
                texts=None, files=None, urls=None, allow_network=None,
                required_sections=None, allowed_tools=None,
                profile=None) -> TaskRequest:
        """从原始请求派生子请求：只覆盖给定字段，其余原样保留（D1-01 契约）。"""
        fields: dict = {"task": task}
        if texts is not None:
            fields["texts"] = tuple(texts)
        if files is not None:
            fields["files"] = tuple(files)
        if urls is not None:
            fields["urls"] = tuple(urls)
        if allow_network is not None:
            fields["allow_network"] = bool(allow_network)
        if required_sections is not None:
            fields["required_sections"] = tuple(required_sections)
        if budget is not None:
            fields["max_calls"] = budget.max_calls
            fields["max_seconds"] = budget.max_seconds
            fields["max_cost"] = budget.max_cost_usd
        if allowed_tools is not None:
            fields["allowed_tools"] = tuple(allowed_tools)
        if profile is not None:
            fields["profile"] = profile
        return dataclasses.replace(request, **fields)

    @staticmethod
    def _effective_role_config(request: TaskRequest, role: str) -> dict:
        """D2-03：角色工具/技能/模型配置生效——权限取"角色声明 ∩ 父级授权"交集。"""
        from src.agents.profiles import get_profile

        try:
            profile = get_profile(role)
        except KeyError:
            return {"role": role, "tools": [], "skills": [],
                    "model_profile": request.profile or "default(入口配置)",
                    "profile_override": None,
                    "role_declared": False}
        parent_allowed = request.allowed_tools          # None = 入口未限制
        if parent_allowed is None:
            effective_tools = tuple(profile.tools)
        else:
            effective_tools = tuple(t for t in profile.tools if t in parent_allowed)
        model_profile = request.profile or (
            profile.model_profile if profile.model_profile != "default" else None)
        return {"role": role, "tools": list(effective_tools),
                "role_declared_tools": list(profile.tools),
                "skills": list(profile.skills),
                "model_profile": model_profile or "default(入口配置)",
                "profile_override": model_profile,
                "role_declared": True}

    # ---- 入口 ---------------------------------------------------------------
    def execute_plan(self, request: TaskRequest, plan: ExecutionPlan, *,
                     budget_caps: Budget | None = None,
                     plan_meta: dict | None = None,
                     root_job_id: str | None = None,
                     should_stop=None) -> dict:
        """执行方案，返回研究过程记录（方案、派工、花费、失败与降级，全部落盘可查）。

        D1-02：root_job_id 应由入口在**调度前**预留（见 reserve_root_job）并传入；
        未传入时此处补建。根任务目录的 orchestration.json 随执行推进更新，
        列出调度方案、全部子任务（child）与最终交付；子任务只记父子关系，
        不新开独立预算根（预算根统一在 D2 网关层强制）。
        """
        if root_job_id is None:
            root_job_id = "job_" + uuid.uuid4().hex
        if should_stop is None and self.state_queue is not None:
            def should_stop():
                row = self.state_queue.get(root_job_id) or {}
                return bool(row.get("cancel_requested"))
        self._should_stop = should_stop or (lambda: False)
        self._cancelled = False
        caps = budget_caps if budget_caps is not None else caps_of(request)
        record: dict = {
            "schema_version": 2, "root_job_id": root_job_id,
            "topic": request.task, "mode_requested": plan.mode,
            "orchestration": request.orchestration,
            "status": "executing",
            "plan": plan.as_dict(), "plan_meta": dict(plan_meta or {}),
            "subtasks": [], "root_job_id": root_job_id, "draft_level": None,
            "degraded": False, "failures": [],
            "execution_note": "受根账本锁限制，本轮子任务按序执行（并行开放见 D2-02）",
        }
        self._write_root_record(record)
        if self.state_queue is not None:
            try:
                self.state_queue.submit(
                    job_id=root_job_id, kind="research",
                    request=request.snapshot(), stage="orchestration", plan_version=1)
            except Exception:
                if self.state_queue.get(root_job_id) is None:
                    raise
        from src.application.orchestration.registry import handler_name
        handler = handler_name(plan.mode)
        if not handler:
            raise PlanValidationError(f"执行器尚未注册模式 {plan.mode!r}")
        getattr(self, handler)(request, plan, caps=caps, record=record)
        if self._cancelled:
            record["draft_level"] = record.get("draft_level") or "draft"
            record["termination_reason"] = "cancelled"
        record["status"] = "finished"
        from src.application.orchestration.contracts import unified_record
        record["unified"] = unified_record(
            root_job_id=root_job_id, draft_level=record.get("draft_level"),
            termination_reason=record.get("termination_reason", ""),
            message=record.get("message", ""))
        self._write_root_record(record)
        return record

    # ---- 根任务记录（D1-02）-------------------------------------------------
    def _write_root_record(self, record: dict) -> None:
        if self.state_queue is not None:
            try:
                self.state_queue.update_progress(
                    record["root_job_id"], stage=record.get("status", ""),
                    message=record.get("message", ""))
            except LookupError:
                pass
        if not self.workspace_root:
            return
        job_dir = Path(self.workspace_root) / "jobs" / record["root_job_id"]
        job_dir.mkdir(parents=True, exist_ok=True)
        write_json(job_dir / "orchestration.json", record)

    def _adopt_root_payload(self, job_dir, plan: ExecutionPlan, record: dict) -> None:
        """最终交付运行结束后，把编排信息（方案/子任务/降级）并入根 job.json。"""
        job_json = Path(job_dir) / "job.json"
        if not job_json.exists():
            return
        payload = json.loads(job_json.read_text(encoding="utf-8"))
        from src.application.orchestration.refs import build_root_lineage
        lineage = build_root_lineage(job_dir.parent.parent, job_dir.name,
                                     record.get("subtasks", []))
        payload["orchestration"] = {
            "plan": plan.as_dict(), "plan_meta": record.get("plan_meta") or {},
            "children": record.get("subtasks", []),
            "citation_lineage": lineage,
            "degraded": record.get("degraded", False),
            "failures": record.get("failures", []),
            "budget_split": record.get("budget_split") or {}}
        write_json(job_dir / "citation_lineage.json", {
            "schema_version": 1, "root_job_id": job_dir.name,
            "lineage": lineage})
        write_json(job_json, payload)

    # ---- D3-05：根任务共享来源库 -------------------------------------------
    def _prepare_shared_sources(self, request, record, *, root_ledger=None):
        """在根任务首次导入资料/执行搜索，供全部子任务复用，避免重复下载。"""
        root_dir = self._root_job_dir(record)
        if root_dir is None:
            return None, []
        from src.harness.storage.sources import SourceImportError, SourceStore
        library_dir = root_dir / "shared_sources"
        store = SourceStore(library_dir)
        if not (library_dir / "sources.json").exists():
            effective = request
            if (request.allow_network and self.settings is not None
                    and getattr(self.settings, "search_provider", "")):
                from src.application.web_research import auto_search_candidates
                from src.harness.ingest.site_policy import build_domain_blocklist
                from src.harness.storage.sources import MAX_SOURCES
                try:
                    remaining = max(0, MAX_SOURCES - len(request.texts)
                                    - len(request.files) - len(request.urls))
                    found, _ = auto_search_candidates(
                        request.task, provider=self.settings.search_provider,
                        run_mode=request.mode, llm=self.llm,
                        max_results=getattr(self.settings, "search_max_results", 8),
                        max_candidates=remaining,
                        known_urls=set(request.urls),
                        on_search=(root_ledger.record_search if root_ledger else None),
                        blocked_domains=build_domain_blocklist(
                            self.settings).domains_for_filter())
                    if found:
                        effective = dataclasses.replace(
                            request, urls=request.urls + tuple(
                                item["url"] for item in found))
                except Exception as e:  # noqa: BLE001 —— 搜索失败如实记入编排
                    record["failures"].append(
                        f"共享来源搜索失败：{type(e).__name__}: {e}")
                    record["degraded"] = True
            if effective.texts or effective.files or effective.urls:
                from src.application.imports import import_request_sources
                from src.harness.ingest.url_policy import UrlPolicy
                try:
                    import_request_sources(library_dir, effective, url_policy=UrlPolicy())
                except SourceImportError as e:
                    record["failures"].append(f"共享来源导入失败：{e}")
                    record["degraded"] = True
        summary = store.summary()
        write_json(root_dir / "source_library.json", {
            "schema_version": 1, "root_job_id": record["root_job_id"],
            "store_dir": "shared_sources", "sources": summary["sources"],
            "retrieved_once": True,
        })
        return store, [text for _, text in store.usable_texts()]

    # ---- 模式实现 -----------------------------------------------------------
    def _register_child_state(self, child_id: str, parent_id: str,
                              request, outcome) -> None:
        if self.state_queue is None or not child_id:
            return
        stage = getattr(outcome, "termination_reason", "") or "child_running"
        try:
            self.state_queue.submit(
                job_id=child_id, kind="research", request=request.snapshot(),
                stage=stage, parent_job_id=parent_id, plan_version=1)
        except Exception:
            self.state_queue.update_progress(child_id, stage=stage,
                                             message="child resumed/updated")

    def _root_job_dir(self, record: dict):
        return (Path(self.workspace_root) / "jobs" / record["root_job_id"]
                if self.workspace_root else None)

    def _mark_cancelled(self, record: dict, message: str = "收到取消请求，停止新分支") -> None:
        self._cancelled = True
        record["degraded"] = True
        record["termination_reason"] = "cancelled"
        record["message"] = message
        record["failures"].append(message)

    def _execute_single(self, request, plan, *, caps, record) -> None:
        """D6-01：简单任务直接进入统一根任务，不走多子任务运行时。"""
        outcome = self._run_request(request, job_id=record["root_job_id"])
        record["draft_level"] = getattr(outcome, "draft_level", None)
        record["termination_reason"] = getattr(outcome, "termination_reason", "")
        record["message"] = getattr(outcome, "message", "")
        job_dir = self._root_job_dir(record)
        if job_dir:
            self._adopt_root_payload(job_dir, plan, record)

    def _execute_fixed(self, request, plan, *, caps, record) -> None:
        # fixed：原请求即根请求（含文件/模型/零限额等全部字段），根 job 即交付 job
        outcome = self._run_request(request, job_id=record["root_job_id"])
        record["draft_level"] = getattr(outcome, "draft_level", None)
        record["termination_reason"] = getattr(outcome, "termination_reason", "")
        record["message"] = getattr(outcome, "message", "")
        job_dir = self._root_job_dir(record)
        if job_dir:
            self._adopt_root_payload(job_dir, plan, record)

    def _execute_debate(self, request, plan, *, caps, record) -> None:
        """D6-06：正反双方并发引据，再由审查/裁决调用汇总支持与争议。"""
        from contextlib import nullcontext
        from src.application.orchestration.plan_contract import SubTask
        from src.harness.model_gateway import JobLedger, job_scope, model_call
        from src.harness.storage.sources import SourceStore

        guards = OrchestrationGuards()
        for st in plan.subtasks:
            try:
                guards.register_subtask(st.id, st.description, depth=1)
            except GuardViolation as e:
                record["failures"].append(f"护栏拒绝派生：{e}")
                record["degraded"] = True

        root_ledger = None
        if self.workspace_root:
            root_ledger = JobLedger(
                Path(self.workspace_root) / "jobs" / record["root_job_id"], request)
        shared_store, shared_texts = self._prepare_shared_sources(
            request, record, root_ledger=root_ledger)
        reserve = final_reserve_of(caps)
        pool_budget = compute_dispatchable_pool(caps, final_reserve=reserve)
        shares = allocate_budget(pool_budget, 2)
        if self._should_stop():
            self._mark_cancelled(record)
            return
        sides = [
            SubTask(id="PRO", role="researcher", description=f"支持方论据：{request.task}",
                    parallel=True),
            SubTask(id="CON", role="editor", description=f"反对方论据与反驳：{request.task}",
                    parallel=True),
        ]

        def run_side(item):
            side, share = item
            effective = self._effective_role_config(request, side.role)
            reservation_id = None
            if root_ledger is not None:
                reservation_id = root_ledger.reserve(
                    share.max_cost_usd, purpose=f"debate:{side.id}", ref_id=side.id)
            outcome, failure, attempts, child_id = self._run_subtask(
                request, side, share, attempts=1,
                parent_job_id=record["root_job_id"], shared_texts=shared_texts,
                effective_tools=(tuple(effective["tools"])
                                 if effective.get("role_declared") else None),
                effective_profile=effective.get("profile_override"))
            draft_level = getattr(outcome, "draft_level", None) if outcome else None
            cost_usd = None
            if child_id and self.workspace_root:
                ledger_path = (Path(self.workspace_root) / "jobs" / child_id
                               / "ledger.json")
                if ledger_path.exists():
                    try:
                        cost_usd = json.loads(
                            ledger_path.read_text(encoding="utf-8")).get("estimated_cost_usd")
                    except ValueError:
                        cost_usd = None
            if reservation_id and root_ledger is not None:
                root_ledger.settle(reservation_id, cost_usd, child_job_id=child_id,
                                   role=side.role, draft_level=draft_level,
                                   attempts=attempts)
            if child_id and self.workspace_root and shared_store is not None:
                SourceStore(Path(self.workspace_root) / "jobs" / child_id)\
                    .link_source_library(shared_store, source_job_id=record["root_job_id"])
            entry = {"id": side.id, "role": side.role, "description": side.description,
                     "child_job_id": child_id, "parent_job_id": record["root_job_id"],
                     "budget": share.as_dict(), "cost_usd": cost_usd,
                     "draft_level": draft_level, "attempts": attempts,
                     "failure": failure or "", "effective": effective, "result": None}
            if child_id and self.workspace_root:
                from src.application.orchestration.refs import collect_child_refs
                sub_result = collect_child_refs(Path(self.workspace_root), child_id)
                sub_result.role = side.role
                sub_result.draft_level = draft_level or ""
                text = getattr(outcome, "final_text", "") or ""
                sub_result.summary = text[:500]
                entry["result"] = sub_result.to_dict()
            return entry, outcome, failure

        with ThreadPoolExecutor(max_workers=2) as side_pool:
            futures = [side_pool.submit(copy_context().run, run_side, item)
                       for item in zip(sides, shares)]
            side_results = [future.result() for future in futures]
        for entry, _, _ in side_results:
            record["subtasks"].append(entry)
        self._write_root_record(record)
        pro_text = getattr(side_results[0][1], "final_text", "") or ""
        con_text = getattr(side_results[1][1], "final_text", "") or ""
        judge_prompt = (
            f"问题：{request.task}\n\n【支持方】\n{pro_text}\n\n"
            f"【反对方】\n{con_text}\n\n请形成“支持点、争议点、证据缺口”清单；"
            "不得凭角色投票宣布事实成立，信息不足就明确写出缺口。")
        with (job_scope(root_ledger) if root_ledger is not None else nullcontext()):
            judge = model_call(self.llm, [{"role": "user", "content": judge_prompt}],
                               purpose="judge", role="judge")
        verdict = (judge.content or "").strip() or "（裁决者未输出）"
        if self._should_stop():
            self._mark_cancelled(record)
            return
        record["debate"] = {"verdict": verdict, "pro": pro_text[:500],
                            "con": con_text[:500]}
        integrated = tuple(shared_texts) + (
            f"[辩论支持方]\n{pro_text}",
            f"[辩论反对方]\n{con_text}",
            f"[辩论审查清单]\n{verdict}",
        )
        root = self._derive(
            request, task=request.task, budget=reserve, texts=integrated,
            files=(), urls=(), allow_network=False,
            required_sections=request.required_sections)
        with (job_scope(root_ledger) if root_ledger is not None else nullcontext()):
            outcome = self._run_request(root, job_id=record["root_job_id"])
        record["draft_level"] = getattr(outcome, "draft_level", None)
        record["termination_reason"] = getattr(outcome, "termination_reason", "")
        record["message"] = getattr(outcome, "message", "")
        record["budget_split"] = {"root": caps.as_dict(), "final_reserve": reserve.as_dict(),
                                  "pool": pool_budget.as_dict()}
        job_dir = self._root_job_dir(record)
        if job_dir:
            self._adopt_root_payload(job_dir, plan, record)
    def _execute_dynamic_team(self, request, plan, *, caps, record) -> None:
        """D6-05：动态规划/重规划 + 有界并发；新增任务仍受根预算和派生护栏限制。"""
        from contextlib import nullcontext
        from src.application.orchestration.plan_contract import SubTask
        from src.harness.model_gateway import JobLedger, job_scope
        from src.orchestration.dynamic_team import run_dynamic_team
        from src.harness.storage.sources import SourceStore

        guards = OrchestrationGuards()
        for st in plan.subtasks:
            try:
                guards.register_subtask(st.id, st.description, depth=1)
            except GuardViolation as e:
                record["failures"].append(f"护栏拒绝派生：{e}")
                record["degraded"] = True

        root_ledger = None
        if self.workspace_root:
            root_ledger = JobLedger(
                Path(self.workspace_root) / "jobs" / record["root_job_id"], request)
        shared_store, shared_texts = self._prepare_shared_sources(
            request, record, root_ledger=root_ledger)
        reserve = final_reserve_of(caps)
        pool_budget = compute_dispatchable_pool(caps, final_reserve=reserve)
        max_parallel = max(1, plan.max_parallel)
        shares = allocate_budget(pool_budget, max_parallel)
        share = shares[0] if shares else Budget()
        entries: list[dict] = []
        outputs: list[str] = []

        def worker(task_text: str, role: str) -> str:
            role_name = role if role in ("researcher", "organizer", "writer", "editor", "agent") \
                else "agent"
            subtask = SubTask(id=f"D{len(entries) + 1}", role=role_name,
                              description=task_text, parallel=True)
            effective = self._effective_role_config(request, role_name)
            reservation_id = None
            if root_ledger is not None:
                reservation_id = root_ledger.reserve(
                    share.max_cost_usd, purpose=f"dynamic:{role_name}",
                    ref_id=subtask.id)
            outcome, failure, attempts, child_id = self._run_subtask(
                request, subtask, share, attempts=1,
                parent_job_id=record["root_job_id"], shared_texts=shared_texts,
                effective_tools=(tuple(effective["tools"])
                                 if effective.get("role_declared") else None),
                effective_profile=effective.get("profile_override"))
            draft_level = getattr(outcome, "draft_level", None) if outcome else None
            cost_usd = None
            if child_id and self.workspace_root:
                ledger_path = (Path(self.workspace_root) / "jobs" / child_id
                               / "ledger.json")
                if ledger_path.exists():
                    try:
                        cost_usd = json.loads(
                            ledger_path.read_text(encoding="utf-8")).get("estimated_cost_usd")
                    except ValueError:
                        cost_usd = None
            if reservation_id and root_ledger is not None:
                root_ledger.settle(reservation_id, cost_usd, child_job_id=child_id,
                                   role=role_name, draft_level=draft_level,
                                   attempts=attempts)
            if child_id and self.workspace_root and shared_store is not None:
                SourceStore(Path(self.workspace_root) / "jobs" / child_id)\
                    .link_source_library(shared_store, source_job_id=record["root_job_id"])
            entry = {"id": subtask.id, "role": role_name, "description": task_text,
                     "child_job_id": child_id, "parent_job_id": record["root_job_id"],
                     "budget": share.as_dict(), "cost_usd": cost_usd,
                     "draft_level": draft_level, "attempts": attempts,
                     "failure": failure or "", "effective": effective, "result": None}
            if child_id and self.workspace_root:
                from src.application.orchestration.refs import collect_child_refs
                sub_result = collect_child_refs(Path(self.workspace_root), child_id)
                sub_result.role = role_name
                sub_result.draft_level = draft_level or ""
                final_text = getattr(outcome, "final_text", "") or ""
                sub_result.summary = final_text[:500]
                entry["result"] = sub_result.to_dict()
            entries.append(entry)
            record["subtasks"] = entries
            self._write_root_record(record)
            if outcome is None or draft_level not in ("accepted", "draft", "unable"):
                raise RuntimeError(failure or "动态子任务未达标")
            text = getattr(outcome, "final_text", "") or ""
            outputs.append(text)
            return text

        if self._should_stop():
            self._mark_cancelled(record)
            return
        with (job_scope(root_ledger) if root_ledger is not None else nullcontext()):
            strategy = run_dynamic_team(request.task, worker, self.llm,
                                        max_parallel=max_parallel)
        record["dynamic_team"] = {
            "worker_calls": strategy.worker_calls,
            "stages": strategy.stages,
        }
        if self._should_stop():
            self._mark_cancelled(record)
            return
        if not outputs:
            record["degraded"] = True
        integrated = tuple(shared_texts) + tuple(
            f"[动态子任务产出]\n{text}" for text in outputs)
        root = self._derive(
            request, task=request.task, budget=reserve, texts=integrated,
            files=(), urls=(), allow_network=False,
            required_sections=request.required_sections)
        outcome = self._run_request(root, job_id=record["root_job_id"])
        record["draft_level"] = getattr(outcome, "draft_level", None)
        record["termination_reason"] = getattr(outcome, "termination_reason", "")
        record["message"] = getattr(outcome, "message", "")
        record["budget_split"] = {"root": caps.as_dict(), "final_reserve": reserve.as_dict(),
                                  "pool": pool_budget.as_dict()}
        job_dir = self._root_job_dir(record)
        if job_dir:
            self._adopt_root_payload(job_dir, plan, record)
    def _execute_fanout(self, request, plan, *, caps, record) -> None:
        """D6-04：按依赖波次真实有界并发；每个子任务先预留预算再启动。"""
        guards = OrchestrationGuards()
        research_tasks = []
        try:
            for st in plan.subtasks:
                if st.role in ("researcher", "organizer", "editor", "agent"):
                    guards.register_subtask(st.id, st.description, depth=1)
                    research_tasks.append(st)
        except GuardViolation as e:
            record["failures"].append(f"护栏拒绝派生：{e}")
            record["degraded"] = True

        by_id = {st.id: st for st in research_tasks}
        remaining = {st.id: st for st in research_tasks}
        completed_ids: set[str] = set()
        results: dict[str, str] = {}
        sub_texts: list[str] = []

        root_ledger = None
        if self.workspace_root:
            from src.harness.model_gateway import JobLedger
            root_ledger = JobLedger(
                Path(self.workspace_root) / "jobs" / record["root_job_id"], request)
        shared_store, shared_texts = self._prepare_shared_sources(
            request, record, root_ledger=root_ledger)
        reserve = final_reserve_of(caps)
        pool_budget = compute_dispatchable_pool(caps, final_reserve=reserve)
        shares = allocate_budget(pool_budget, len(research_tasks)) if research_tasks else []
        share_by_id = {st.id: share for st, share in zip(research_tasks, shares)}
        max_parallel = max(1, min(plan.max_parallel, len(research_tasks) or 1))
        record["execution_note"] = (
            f"fanout 按依赖波次并发，max_parallel={max_parallel}；"
            "每任务先预留，完成后结算")

        def run_one(st, share, effective, prior_texts):
            return self._run_subtask(
                request, st, share, attempts=2,
                parent_job_id=record["root_job_id"], shared_texts=shared_texts,
                prior_texts=prior_texts,
                effective_tools=(tuple(effective["tools"])
                                 if effective.get("role_declared") else None),
                effective_profile=effective.get("profile_override"))

        while remaining:
            if self._should_stop():
                self._mark_cancelled(record)
                return
            ready = []
            for st in remaining.values():
                deps = list(st.depends_on)
                if all(dep in completed_ids for dep in deps):
                    ready.append(st)
                elif any(dep not in by_id or dep not in completed_ids for dep in deps) \
                        and all(dep in by_id and dep not in remaining
                                for dep in deps):
                    # 依赖已处理但未完成：当前任务不再等待，下一波显式失败。
                    ready.append(st)
            if not ready:
                for st in list(remaining.values()):
                    share = share_by_id.get(st.id, Budget())
                    record["subtasks"].append({
                        "id": st.id, "role": st.role, "description": st.description,
                        "child_job_id": "", "parent_job_id": record["root_job_id"],
                        "budget": share.as_dict(), "cost_usd": None,
                        "draft_level": None, "attempts": 0,
                        "failure": f"前置依赖未完成：{list(st.depends_on)}",
                        "result": None,
                        "effective": self._effective_role_config(request, st.role)})
                    remaining.pop(st.id, None)
                record["degraded"] = True
                break

            wave = ready[:max_parallel]
            prepared = []
            for st in wave:
                unresolved = [dep for dep in st.depends_on if dep not in completed_ids]
                share = share_by_id.get(st.id, Budget())
                effective = self._effective_role_config(request, st.role)
                if unresolved:
                    record["subtasks"].append({
                        "id": st.id, "role": st.role, "description": st.description,
                        "child_job_id": "", "parent_job_id": record["root_job_id"],
                        "budget": share.as_dict(), "cost_usd": None,
                        "draft_level": None, "attempts": 0,
                        "failure": f"前置依赖未完成：{unresolved}",
                        "result": None, "effective": effective})
                    remaining.pop(st.id, None)
                    record["degraded"] = True
                    continue
                reservation_id = None
                if root_ledger is not None:
                    try:
                        reservation_id = root_ledger.reserve(
                            share.max_cost_usd, purpose=f"child:{st.id}", ref_id=st.id)
                    except BudgetStop as e:
                        record["subtasks"].append({
                            "id": st.id, "role": st.role, "description": st.description,
                            "child_job_id": "", "parent_job_id": record["root_job_id"],
                            "budget": share.as_dict(), "cost_usd": None,
                            "draft_level": None, "attempts": 0,
                            "failure": f"预算预留失败：{e}", "result": None,
                            "effective": effective})
                        remaining.pop(st.id, None)
                        record["degraded"] = True
                        continue
                prepared.append((st, share, effective, reservation_id))

            if not prepared:
                continue
            if self._should_stop():
                self._mark_cancelled(record)
                return

            with ThreadPoolExecutor(max_workers=len(prepared)) as wave_pool:
                futures = []
                for st, share, effective, reservation_id in prepared:
                    prior_texts = [results[d] for d in st.depends_on if d in results]
                    fut = wave_pool.submit(copy_context().run, run_one,
                                           st, share, effective, prior_texts)
                    futures.append((fut, st, share, effective, reservation_id))
                outputs = [(fut.result(), st, share, effective, reservation_id)
                           for fut, st, share, effective, reservation_id in futures]

            for (outcome, failure, attempts_used, child_job_id), st, share, \
                    effective, reservation_id in outputs:
                draft_level = getattr(outcome, "draft_level", None) if outcome else None
                cost_usd = None
                if child_job_id and self.workspace_root:
                    child_ledger = (Path(self.workspace_root) / "jobs" / child_job_id
                                    / "ledger.json")
                    if child_ledger.exists():
                        try:
                            cost_usd = json.loads(
                                child_ledger.read_text(encoding="utf-8")
                            ).get("estimated_cost_usd")
                        except ValueError:
                            cost_usd = None
                entry = {"id": st.id, "role": st.role, "description": st.description,
                         "child_job_id": child_job_id,
                         "parent_job_id": record["root_job_id"],
                         "budget": share.as_dict(), "cost_usd": cost_usd,
                         "draft_level": draft_level, "attempts": attempts_used,
                         "failure": failure or "", "effective": effective,
                         "result": None}
                if reservation_id and root_ledger is not None:
                    root_ledger.settle(reservation_id, cost_usd, child_job_id=child_job_id,
                                       role=st.role, draft_level=draft_level,
                                       attempts=attempts_used)
                if child_job_id and self.workspace_root and shared_store is not None:
                    from src.harness.storage.sources import SourceStore
                    SourceStore(Path(self.workspace_root) / "jobs" / child_job_id)\
                        .link_source_library(shared_store, source_job_id=record["root_job_id"])
                if child_job_id and self.workspace_root:
                    from src.application.orchestration.refs import collect_child_refs
                    sub_result = collect_child_refs(Path(self.workspace_root), child_job_id)
                    sub_result.role = st.role
                    sub_result.draft_level = draft_level or ""
                    final_text = getattr(outcome, "final_text", "") or ""
                    sub_result.summary = final_text[:500]
                    if len(final_text) > len(sub_result.summary):
                        sub_result.truncation_note = (
                            sub_result.truncation_note
                            + f"；摘要截断至 {len(sub_result.summary)} 字").lstrip("；")
                    entry["result"] = sub_result.to_dict()
                record["subtasks"].append(entry)
                self._write_root_record(record)
                if outcome is not None and draft_level in ("accepted", "draft", "unable"):
                    completed_ids.add(st.id)
                if outcome is not None and (getattr(outcome, "final_text", "") or "").strip():
                    results[st.id] = outcome.final_text
                    sub_texts.append(_SUB_MARKER.format(
                        sid=st.id, role=st.role, desc=st.description[:60],
                        text=outcome.final_text))
                elif failure:
                    record["degraded"] = True
                remaining.pop(st.id, None)

        if self._should_stop():
            self._mark_cancelled(record)
            return
        if caps.max_cost_usd <= 0:
            record["degraded"] = True
            record["failures"].append("零预算：成稿与审核运行未启动（零请求）")
            record["termination_reason"] = "cancelled"
            record["message"] = "零预算：子任务与成稿均未运行（零请求）"
            return
        integrated = tuple(shared_texts) + tuple(sub_texts)
        root = self._derive(
            request, task=request.task, budget=reserve, texts=integrated,
            files=(), urls=(), allow_network=False,
            required_sections=request.required_sections)
        outcome = self._run_request(root, job_id=record["root_job_id"])
        if shared_store is not None and self.workspace_root:
            from src.harness.storage.sources import SourceStore
            SourceStore(Path(self.workspace_root) / "jobs" / record["root_job_id"])\
                .link_source_library(shared_store, source_job_id=record["root_job_id"])
        record["draft_level"] = getattr(outcome, "draft_level", None)
        record["termination_reason"] = getattr(outcome, "termination_reason", "")
        record["message"] = getattr(outcome, "message", "")
        record["budget_split"] = {"root": caps.as_dict(), "final_reserve": reserve.as_dict(),
                                  "pool": pool_budget.as_dict()}
        job_dir = self._root_job_dir(record)
        if job_dir:
            self._adopt_root_payload(job_dir, plan, record)
    def _run_subtask(self, request, subtask, share: Budget, *, attempts: int,
                     parent_job_id: str, shared_texts=None, prior_texts=None,
                     effective_tools=None, effective_profile=None):
        """单个子任务：失败原地重试一次（重试记为新尝试）；仍失败返回 (None, 失败原因, 尝试数, 子job)。

        即使最终未达标，也返回最后一次尝试的 child job id——失败运行同样可追溯。
        effective_tools/effective_profile 为 D2-03 角色交集后的生效配置。
        """
        last_error = ""
        child_id = ""
        for attempt in range(1, attempts + 1):
            try:
                sub = self._derive(
                    request,
                    task=(f"{subtask.description}"
                          "（只整理资料与证据，写小节说明，不写最终报告）"),
                    budget=share, required_sections=(),
                    texts=(tuple(shared_texts or ()) + tuple(
                        f"[前置任务 {index} 产出]\n{text}"
                        for index, text in enumerate(prior_texts or (), start=1))
                           if shared_texts is not None or prior_texts else None),
                    files=(), urls=(), allow_network=False,
                    allowed_tools=effective_tools, profile=effective_profile)
                outcome = self._run_request(sub, parent_job_id=parent_job_id)
                child_id = (getattr(outcome, "root_job_id", "") or child_id)
                self._register_child_state(child_id, parent_job_id, sub, outcome)
                if getattr(outcome, "draft_level", None) in ("accepted", "draft", "unable"):
                    return outcome, None, attempt, child_id
                last_error = (f"第{attempt}次子运行未达标："
                              f"{getattr(outcome, 'termination_reason', '')}")
            except Exception as e:  # noqa: BLE001 —— 失败留痕重试，不中断整批
                last_error = f"第{attempt}次子运行异常：{type(e).__name__}: {e}"
        return None, last_error, attempts, child_id
