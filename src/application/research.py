"""统一应用入口。B2接Runtime；B3/B4资料导入；B5研究写作链；S4续跑入口；S5Web接线参数。"""
from pathlib import Path
import dataclasses
import json
import re
import uuid

from config.settings import Settings
from src.application.imports import import_request_sources
from src.application.pipeline.model import HardRequirements
from src.application.pipeline.runner import run_research_pipeline
from src.application.request import TaskRequest
from src.harness.ingest.search import search_enabled
from src.harness.ingest.url_policy import UrlPolicy
from src.harness.models import factory
from src.harness.model_gateway import BudgetStop, JobLedger, job_scope, check_root_budget
from src.harness.runtime.agent_runtime import AgentRuntime
from src.harness.runtime.run_context import RuntimeContext
from src.harness.run_store import write_json
from src.harness.storage.sources import SourceImportError, SourceStore
from src.harness.tools.executor import ToolExecutor
from src.harness.tools.registry import ToolRegistry

_CHAIN_TO_STATUS = {"success": "completed", "incomplete": "partial",
                    "unable": "partial",
                    "budget_exceeded": "cancelled", "error": "failed",
                    "cancelled": "cancelled"}
_JOB_ID_RE = re.compile(r"^job_[0-9a-f]{32}$")


def _hard_requirements(request) -> HardRequirements:
    """任务硬约束（S6-05 对齐）：请求中的必需章节/禁语/关键事实交给链内程序层复验。"""
    return HardRequirements(required_sections=request.required_sections,
                            forbidden_claims=request.forbidden_claims,
                            key_facts=request.key_facts)


def follow_up_revision(*, workspace_root, job_id: str, instruction: str,
                       llm=None, settings=None) -> object:
    """S5-04 追问改稿：以 job_id 任务的最新报告为原稿，续用其同批资料改写。

    - 原稿 = 原任务最新 report 产物全文（旧版本永不覆盖）；
    - 资料 = 原任务来源全文（复制进新任务，独立可回溯）；
    - 新任务 job.json 记录 revises_job 谱系；退出码/分级语义与 research 一致。
    """
    if not instruction or not instruction.strip():
        raise ValueError("改稿指令不能为空")
    root = Path(workspace_root)
    origin = root / "jobs" / job_id
    if not (origin / "sources.json").exists():
        raise SourceImportError(f"任务 {job_id} 不存在或没有资料，无法追问改稿")
    store = SourceStore(origin)
    texts = [text for source in store.summary()["sources"]
             if source.get("status") in ("ok", "partial") and source.get("file_name")
             for text in [store.full_text(source["source_id"])] if text]
    if not texts:
        raise SourceImportError(f"任务 {job_id} 没有可用来源全文，无法追问改稿")
    artifacts = _latest_report_text(origin)
    if artifacts is None:
        raise SourceImportError(f"任务 {job_id} 没有报告产物，无法作为原稿")
    base_draft, base_kind = artifacts
    snapshot = {}
    request_path = origin / "request.json"
    if request_path.exists():
        try:
            snapshot = json.loads(request_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    request = TaskRequest(task=instruction, mode=snapshot.get("mode") or "mock",
                          profile=snapshot.get("profile"),
                          max_iterations=int(snapshot.get("max_iterations") or 8),
                          max_calls=int(snapshot.get("max_calls") or 12),
                          max_output_tokens=int(snapshot.get("max_output_tokens") or 8192),
                          max_seconds=float(snapshot.get("max_seconds") or 300),
                          max_cost=snapshot.get("max_cost"),
                          texts=tuple(texts), flow="research",
                          delivery_kind=snapshot.get("delivery_kind") or "auto",
                          base_draft=base_draft, revises_job=job_id,
                          required_sections=tuple(snapshot.get("required_sections") or ()),
                          forbidden_claims=tuple(snapshot.get("forbidden_claims") or ()),
                          key_facts=tuple(snapshot.get("key_facts") or ()))
    app = ResearchApplication(request, settings=settings or Settings(),
                              workspace_root=root, llm=llm)
    outcome = app.run()
    setattr(outcome, "revises_job", job_id)
    setattr(outcome, "base_kind", base_kind)
    return outcome


def revise_with_source_update(*, workspace_root, job_id: str, source_id: str,
                               reason: str, instruction: str,
                               llm=None, settings=None) -> object:
    """D7-04：撤回/更新来源后，以剩余已提交来源和最新原稿创建新版本。"""
    if not instruction or not instruction.strip():
        raise ValueError("来源更新后的改稿指令不能为空")
    root = Path(workspace_root)
    origin = root / "jobs" / job_id
    store = SourceStore(origin)
    withdrawal = store.withdraw(source_id, reason)
    texts = []
    for record, text in store.usable_texts():
        if text:
            texts.append(text)
    if not texts:
        raise SourceImportError("撤回来源后已没有可用资料，拒绝继续改稿")
    latest = _latest_report_text(origin)
    if latest is None:
        raise SourceImportError(f"任务 {job_id} 没有报告产物，无法基于来源更新改稿")
    base_draft, _ = latest
    snapshot = {}
    request_path = origin / "request.json"
    if request_path.exists():
        try:
            snapshot = json.loads(request_path.read_text(encoding="utf-8"))
        except Exception:
            snapshot = {}
    request = TaskRequest(
        task=instruction, mode=snapshot.get("mode") or "mock",
        profile=snapshot.get("profile"),
        max_iterations=int(snapshot.get("max_iterations") or 8),
        max_calls=int(snapshot.get("max_calls") or 12),
        max_output_tokens=int(snapshot.get("max_output_tokens") or 8192),
        max_seconds=float(snapshot.get("max_seconds") or 300),
        max_cost=snapshot.get("max_cost"), texts=tuple(texts),
        flow="research", delivery_kind=snapshot.get("delivery_kind") or "auto",
        base_draft=base_draft, revises_job=job_id,
        required_sections=tuple(snapshot.get("required_sections") or ()),
        forbidden_claims=tuple(snapshot.get("forbidden_claims") or ()),
        key_facts=tuple(snapshot.get("key_facts") or ()))
    app = ResearchApplication(request, settings=settings or Settings(),
                              workspace_root=root, llm=llm)
    outcome = app.run()
    new_job = root / "jobs" / outcome.root_job_id
    write_json(new_job / "source_update.json", {
        "schema_version": 1, "origin_job_id": job_id,
        "withdrawn": withdrawal,
        "remaining_sources": [r.get("source_id") for r, _ in store.usable_texts()],
        "policy": "原稿和来源版本保留；新稿仅使用未撤回来源",
    })
    setattr(outcome, "source_update", {"withdrawn": source_id, "reason": reason})
    return outcome

def _latest_report_text(job_dir: Path):
    """返回 (文本, artifact_id)；没有 report 产物返回 None。"""
    from src.harness.storage.artifacts import ArtifactStore
    try:
        store = ArtifactStore(job_dir)
    except Exception:
        return None
    versions = [a for a in store.list() if a.get("kind") == "report"]
    if not versions:
        return None
    latest = sorted(versions, key=lambda a: a.get("version") or 0)[-1]
    try:
        return store.read(latest["artifact_id"]).get("text", ""), latest["artifact_id"]
    except Exception:
        return None
def resume_research_job(*, workspace_root, job_id: str, llm=None,
                        settings=None, state_db=None) -> object:
    """S4-12：对已有研究任务在同一 job 目录内续跑（按检查点跳过已完成阶段）。

    账本续接：新开续跑账本并把原账本调用条目载入（次数/输出Token/费用延续原上限），
    恢复后的模型调用仍全部记账；未知用量条目会如实阻止新调用。
    返回与 ResearchApplication.run 相同形状的结果对象（root_job_id=原任务）。
    """
    root = Path(workspace_root)
    job_dir = root / "jobs" / job_id
    if not (job_dir / "sources.json").exists():
        raise SourceImportError(f"任务 {job_id} 没有资料或不存在，无法续跑研究写作链")
    from src.harness.state.resume import inspect_resume_state
    resume_state = inspect_resume_state(job_dir, state_db=state_db)
    write_json(job_dir / "resume_notes.json", resume_state)
    request_path = job_dir / "request.json"
    if request_path.exists():
        try:
            snapshot = json.loads(request_path.read_text(encoding="utf-8"))
        except Exception:
            raise SourceImportError(f"任务 {job_id} 的 request.json 损坏，拒绝猜测后续跑") from None
    else:
        snapshot = {}
    request = TaskRequest(task=snapshot.get("task") or "（续跑）",
                          mode=snapshot.get("mode") or "mock",
                          profile=snapshot.get("profile"),
                          max_iterations=int(snapshot.get("max_iterations") or 8),
                          max_calls=int(snapshot.get("max_calls") or 12),
                          max_output_tokens=int(snapshot.get("max_output_tokens") or 8192),
                          max_seconds=float(snapshot.get("max_seconds") or 300),
                          max_cost=snapshot.get("max_cost"),
                          system_extra=snapshot.get("system_extra") or "",
                          urls=tuple(snapshot.get("urls") or ()),
                          flow="research",
                          delivery_kind=snapshot.get("delivery_kind") or "auto",
                          base_draft=snapshot.get("base_draft") or "",
                          required_sections=tuple(snapshot.get("required_sections") or ()),
                          forbidden_claims=tuple(snapshot.get("forbidden_claims") or ()),
                          key_facts=tuple(snapshot.get("key_facts") or ()))
    settings = settings or Settings()
    llm = llm if llm is not None else factory.build_adapter(
        request.profile, settings, mode=request.mode)
    jobs = root / "jobs"
    ledger = JobLedger(jobs / ("job_" + uuid.uuid4().hex), request)
    # 续接账本：载入原调用条目，限制延续（S4-10 语义：同一任务总账本口径）
    old_ledger_path = job_dir / "ledger.json"
    if old_ledger_path.exists():
        try:
            old = json.loads(old_ledger_path.read_text(encoding="utf-8"))
            ledger.calls = list(old.get("calls") or [])
            ledger.reservations = list(old.get("reservations") or [])
            ledger.run_ids = list(old.get("run_ids") or [])
            ledger.write()
        except Exception:
            raise SourceImportError("原账本无法解析：续跑前请人工核对已发生调用") from None
    result = None
    with job_scope(ledger):
        try:
            result = run_research_pipeline(
                llm=llm, job_dir=job_dir, store=SourceStore(job_dir),
                goal=request.task, max_revision_rounds=2, resume=True,
                hard_requirements=_hard_requirements(request),
                delivery_kind=request.delivery_kind,
                repair_callback=None)
        except BudgetStop:  # 防御：runner 内已收敛，这里兜底
            result = None
            raise
    status = _CHAIN_TO_STATUS.get(result.termination_reason, "failed") if result else "failed"
    ledger.finish(status)
    payload = {
        "schema_version": 1, "root_job_id": job_id, "status": status,
        "mode": getattr(llm, "run_mode", "custom"), "model": llm.model_name,
        "run_ids": ledger.run_ids, "error": None, "stop_reason": ledger.stop_reason,
        "final_text": result.final_text if result else "",
        "business_acceptance": "not_evaluated",
        "resume": True, "continuation_ledger": ledger.job_id}
    if result is not None:
        payload["pipeline"] = result.as_dict()
    write_json(job_dir / "job.json", payload)
    if result is not None:
        result.root_job_id = job_id
        result.run_id = None
    return result


class ResearchApplication:
    def __init__(self, request: TaskRequest, *, settings=None, workspace_root=None,
                 llm=None, tool_executor=None, url_policy: UrlPolicy | None = None):
        self.request = request
        # factory统一做配置检查；不会因缺Key选择Mock。
        self.llm = llm if llm is not None else factory.build_adapter(request.profile, settings, mode=request.mode)
        if getattr(self.llm, "run_mode", None) != request.mode:
            raise factory.ModelConfigError("实际适配器与请求模式不一致，已停止")
        self.settings = settings or Settings()
        self.root = Path(workspace_root or self.settings.workspace_dir)
        self.executor = tool_executor or ToolExecutor(ToolRegistry.with_builtins())
        # B4：抓取地址策略。默认拒绝私网/回环/链路本地；测试或受信intranet
        # 场景由调用方显式传入 UrlPolicy(allowed_hosts=...)（S2-09 独立策略）。
        self.url_policy = url_policy
        # D2-04：已配置的 MCP Server 纳入同一工具注册表（未配置 = 不接入）。
        # 会话随 run() 结束关闭；配置/启动失败显式报错，不静默降级。
        self.mcp_sessions = []
        if getattr(self.settings, "mcp_servers", None):
            from src.mcp.bootstrap import connect_configured_mcp_servers
            self.mcp_sessions = connect_configured_mcp_servers(
                self.settings, self.executor.registry)

    def _repair_callback(self):
        """D7-03：允许联网且有搜索配置时，对明确缺口做一次有界补搜。"""
        if not self.request.allow_network or not self.settings.search_provider:
            return None

        def repair(gaps):
            from src.application.web_research import auto_search_candidates
            from src.harness.ingest.fetcher import fetch_url
            from src.harness.ingest.html_extract import extract_document
            from src.harness.model_gateway import ACTIVE_JOB
            query = "；".join((g or {}).get("question", "") for g in gaps).strip()
            if not query:
                return []
            ledger = ACTIVE_JOB.get()
            found, _ = auto_search_candidates(
                query, provider=self.settings.search_provider,
                run_mode=self.request.mode, llm=self.llm,
                max_results=self.settings.search_max_results,
                max_candidates=2,
                on_search=(ledger.record_search if ledger else None))
            texts = []
            for item in found[:2]:
                result = fetch_url(item["url"], self.url_policy)
                if result.status != "ok":
                    continue
                extracted = extract_document(result.raw or b"",
                                             result.content_type, result.charset)
                if extracted.get("text", "").strip():
                    texts.append(extracted["text"])
            return texts

        return repair

    def run(self, *, on_event=None, approval_handler=None, job_id: str | None = None,
            parent_job_id: str | None = None,
            on_progress=None, stage_hook=None, should_stop=None):
        request = self.request
        jobs = self.root / "jobs"
        jobs.mkdir(parents=True, exist_ok=True)
        if job_id is not None and not _JOB_ID_RE.fullmatch(job_id):
            raise ValueError("job_id 格式无效（必须是 job_ + 32位十六进制）")
        directory = jobs / (job_id or ("job_" + uuid.uuid4().hex))
        if directory.exists():
            # D1-02：允许采用编排器"调度前预留"的根任务目录——调度阶段只写
            # orchestration.json/request.json/ledger.json（D2-01 调度入账）；
            # 出现链执行产物（pipeline/sources/artifacts/阶段检查点等）仍拒绝。
            pre_schedule = {"orchestration.json", "request.json", "ledger.json"}
            reserved = {p.name for p in directory.iterdir()} - pre_schedule
            if reserved:
                raise FileExistsError(
                    f"任务目录已存在且含执行产物：{directory}；如需续跑请使用 resume 入口")
        ledger = JobLedger(directory, request)
        outcome = None
        chain_result = None
        error = None
        status = "failed"
        import_summary = None
        web_search_info = None
        try:
            with job_scope(ledger):
                # D3-03 自动联网研究：只给主题且允许联网、且已配置搜索提供方时，
                # 先拆查询→真实搜索→候选 URL 并入导入清单（正文读取/去重/分类
                # 沿用既有来源管线；搜索调用经 record_search 入根账本）。
                if (request.flow == "research" and request.allow_network
                        and search_enabled(self.settings.search_provider)):
                    from src.application.web_research import auto_search_candidates
                    try:
                        found, records = auto_search_candidates(
                            request.task, provider=self.settings.search_provider,
                            run_mode=request.mode, llm=self.llm,
                            max_results=self.settings.search_max_results,
                            known_urls=set(request.urls),
                            on_search=ledger.record_search)
                        found_urls = [item["url"] for item in found]
                        if found_urls:
                            request = dataclasses.replace(
                                request, urls=request.urls + tuple(found_urls))
                        all_failed = bool(records) and all(
                            record.get("error") for record in records)
                        web_search_info = {
                            "status": "ok" if found_urls else
                                      ("failed" if all_failed else "no_results"),
                            "provider": self.settings.search_provider,
                            "added_urls": found_urls, "candidates": found,
                            "records": records}
                    except Exception as e:  # noqa: BLE001 —— 搜索失败显式呈现，继续用给定资料
                        web_search_info = {"status": "failed",
                                           "error": f"{type(e).__name__}: {e}"[:200]}
                # B3/B4：资料（文本/文件/用户URL）先导入并登记；失败来源明确分类；
                # 一个可用来源都没有时任务不启动，不会带着"空资料"假成功。
                store = None
                if request.texts or request.files or request.urls:
                    store = import_request_sources(ledger.directory, request,
                                                   url_policy=self.url_policy)
                    summary = store.summary()
                    import_summary = {"total": summary["total"], "usable": summary["usable"],
                                      "statuses": summary["statuses"]}
                if request.flow == "research":
                    # B5/S5-04：研究写作链（固定阶段，产物全落 job 目录；预算停止由
                    # runner 收敛为"待完善草稿"语义；base_draft 提供改稿模式）。
                    chain_result = run_research_pipeline(
                        llm=self.llm, job_dir=ledger.directory,
                        store=store or SourceStore(ledger.directory),
                        goal=request.task, max_revision_rounds=2,
                        on_progress=on_progress, stage_hook=stage_hook,
                        should_stop=should_stop,
                        initial_draft=request.base_draft or None,
                        hard_requirements=_hard_requirements(request),
                        delivery_kind=request.delivery_kind,
                        repair_callback=self._repair_callback())
                    outcome = chain_result
                    outcome.root_job_id = ledger.job_id
                    outcome.run_id = None
                    status = _CHAIN_TO_STATUS.get(chain_result.termination_reason, "failed")
                else:
                    context = RuntimeContext.from_settings(self.settings,
                        max_iterations=request.max_iterations, workspace_path=self.root,
                        permissions=frozenset(t.name for t in self.executor.registry.list()),
                        model_profile=request.profile or "default")
                    outcome = AgentRuntime(self.llm, settings=self.settings,
                                           tool_executor=self.executor).run_task(
                        request.task, context=context, system_extra=request.system_extra,
                        on_event=on_event, approval_handler=approval_handler)
                    status = "completed" if outcome.termination_reason == "success" else "partial"
                    try:
                        check_root_budget()
                    except BudgetStop:
                        status = "cancelled"
                    if outcome.termination_reason == "budget_exceeded":
                        status = "cancelled"
            return outcome
        except BaseException as e:
            error = type(e).__name__
            raise
        finally:
            ledger.finish(status)
            for session in getattr(self, "mcp_sessions", []):
                session.close()     # D2-04：MCP 子进程生命周期收尾
            self.mcp_sessions = []
            payload = {
                "schema_version": 1, "root_job_id": ledger.job_id, "status": status,
                "mode": getattr(self.llm, "run_mode", "custom"), "model": self.llm.model_name,
                "run_ids": ledger.run_ids, "error": error, "stop_reason": ledger.stop_reason,
                "final_text": outcome.final_text if outcome else "",
                "business_acceptance": "not_evaluated"}
            if import_summary is not None:
                payload["import"] = import_summary
            if chain_result is not None:
                payload["pipeline"] = chain_result.as_dict()
            if request.revises_job:
                payload["revises_job"] = request.revises_job
            if web_search_info is not None:
                payload["web_search"] = web_search_info
            if parent_job_id:
                # D1-02：子任务只记父子关系，不新开独立预算根（预算根见 job 的 budget_root 链）
                payload["parent_job_id"] = parent_job_id
                payload["budget_root"] = parent_job_id
            write_json(ledger.directory / "job.json", payload)
