"""统一应用入口。B2接Runtime；B3/B4资料导入；B5研究写作链；S4续跑入口；S5Web接线参数。"""
from pathlib import Path
import json
import re
import uuid

from config.settings import Settings
from src.application.imports import import_request_sources
from src.application.pipeline.runner import run_research_pipeline
from src.application.request import TaskRequest
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
                    "budget_exceeded": "cancelled", "error": "failed",
                    "cancelled": "cancelled"}
_JOB_ID_RE = re.compile(r"^job_[0-9a-f]{32}$")


def resume_research_job(*, workspace_root, job_id: str, llm=None,
                        settings=None) -> object:
    """S4-12：对已有研究任务在同一 job 目录内续跑（按检查点跳过已完成阶段）。

    账本续接：新开续跑账本并把原账本调用条目载入（次数/输出Token/费用延续原上限），
    恢复后的模型调用仍全部记账；未知用量条目会如实阻止新调用。
    返回与 ResearchApplication.run 相同形状的结果对象（root_job_id=原任务）。
    """
    root = Path(workspace_root)
    job_dir = root / "jobs" / job_id
    if not (job_dir / "sources.json").exists():
        raise SourceImportError(f"任务 {job_id} 没有资料或不存在，无法续跑研究写作链")
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
                          base_draft=snapshot.get("base_draft") or "")
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
            ledger.run_ids = list(old.get("run_ids") or [])
            ledger.write()
        except Exception:
            raise SourceImportError("原账本无法解析：续跑前请人工核对已发生调用") from None
    result = None
    with job_scope(ledger):
        try:
            result = run_research_pipeline(
                llm=llm, job_dir=job_dir, store=SourceStore(job_dir),
                goal=request.task, max_revision_rounds=2, resume=True)
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

    def run(self, *, on_event=None, approval_handler=None, job_id: str | None = None,
            on_progress=None, stage_hook=None, should_stop=None):
        request = self.request
        jobs = self.root / "jobs"
        jobs.mkdir(parents=True, exist_ok=True)
        if job_id is not None and not _JOB_ID_RE.fullmatch(job_id):
            raise ValueError("job_id 格式无效（必须是 job_ + 32位十六进制）")
        directory = jobs / (job_id or ("job_" + uuid.uuid4().hex))
        if directory.exists():
            raise FileExistsError(f"任务目录已存在：{directory}；如需续跑请使用 resume 入口")
        ledger = JobLedger(directory, request)
        outcome = None
        chain_result = None
        error = None
        status = "failed"
        import_summary = None
        try:
            with job_scope(ledger):
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
                        initial_draft=request.base_draft or None)
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
            write_json(ledger.directory / "job.json", payload)
