"""统一任务入口的命令行客户端。默认Mock；不在命令行接收密钥。"""
import argparse
import json
import sys

from src.application.request import TaskRequest
from src.application.research import (ResearchApplication, follow_up_revision,
                                      resume_research_job)
from src.harness.models.factory import ModelConfigError


def _print_result(result, *, workspace=None) -> int:
    from pathlib import Path
    from config.settings import Settings
    payload = {"root_job_id": result.root_job_id, "run_id": getattr(result, "run_id", None),
               "termination_reason": result.termination_reason,
               "final_text": result.final_text}
    if getattr(result, "draft_level", None):
        payload["draft_level"] = result.draft_level
        payload["message"] = getattr(result, "message", "")
    root = Path(workspace) if workspace else Settings().workspace_dir
    index_path = root / "jobs" / (result.root_job_id or "") / "sources.json"
    if index_path.exists():
        try:
            records = json.loads(index_path.read_text(encoding="utf-8")).get("sources", [])
        except Exception:
            records = []
        counts: dict[str, int] = {}
        for record in records:
            counts[record.get("status", "?")] = counts.get(record.get("status", "?"), 0) + 1
        payload["sources"] = {"total": len(records),
                              "usable": counts.get("ok", 0) + counts.get("partial", 0),
                              "statuses": counts}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.termination_reason == "success" else 1


def _run_orchestrated(request, *, workspace, plan_only) -> int:
    """D1-01/02/S8 入口：调度前预留根任务 → 调度选型 → 按方案派工 → 过程记录落盘。

    request 是统一任务请求（含文件/模型/预算/硬要求等全部字段），原样传给执行器。
    """
    import time
    import uuid
    from pathlib import Path

    from config.settings import Settings
    from src.application.orchestration import (OrchestrationExecutor,
                                               OrchestrationScheduler,
                                               caps_of)
    from src.harness.run_store import write_json

    settings = Settings()
    llm = None
    if request.mode == "real":
        from src.harness.models import factory
        llm = factory.build_adapter(request.profile, settings, mode="real")
    # D1-02：root_job 先于调度创建，编排全程挂在同一根记录下
    root_job_id = "job_" + uuid.uuid4().hex
    root_dir = (Path(workspace) if workspace else settings.workspace_dir) / "jobs" / root_job_id
    root_dir.mkdir(parents=True, exist_ok=True)
    write_json(root_dir / "orchestration.json",
               {"schema_version": 2, "root_job_id": root_job_id,
                "topic": request.task, "status": "reserved",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    caps = caps_of(request)
    source_count = len(request.texts) + len(request.files) + len(request.urls)
    summary = (f"来源 {source_count} 项" + ("（含用户指定链接）" if request.urls else "")
               if source_count else "未提供资料（搜索未接入，缺资料时明确说明）")
    allowed = (("fixed", "fanout") if request.orchestration == "auto"
               else (request.orchestration,))
    # D2-01：调度调用统一经根网关记账（purpose=orchestration_plan, role=scheduler）
    from src.harness.model_gateway import JobLedger, job_scope
    ledger = JobLedger(root_dir, request)
    scheduler = OrchestrationScheduler(llm)
    started = time.time()
    with job_scope(ledger):
        plan, meta = scheduler.plan(request.task, material_summary=summary,
                                    constraints=(f"模式限制 {list(allowed)}；"
                                                 f"预算上限 {caps.as_dict()}"),
                                    required_sections=request.required_sections,
                                    budget_caps=caps, allowed_modes=allowed)
    meta["scheduler_elapsed_seconds"] = round(time.time() - started, 3)
    meta["scheduler_calls"] = ledger.summary()["call_count"]
    ledger.finish("completed")
    # 方案展示走 stderr：stdout 保持单个可解析 JSON（方式告知最终也并入结果载荷）
    print(json.dumps({"orchestration_plan": plan.as_dict(), "plan_meta": meta,
                      "root_job_id": root_job_id},
                     ensure_ascii=False, indent=2), file=sys.stderr)
    if plan_only:
        return 0
    executor = OrchestrationExecutor(workspace_root=workspace, settings=settings, llm=None)
    record = executor.execute_plan(request, plan, budget_caps=caps, plan_meta=meta,
                                   root_job_id=root_job_id)
    record["mode_effective"] = plan.mode
    # D2-02：子任务成本已由执行器经 reserve/settle 入根账本（含未知保守入账），
    # CLI 不再重复汇总。
    root = Path(workspace) if workspace else settings.workspace_dir
    job_dir = root / "jobs" / (record.get("root_job_id") or "")
    # 过程记录由执行器维护并随执行推进写入（orchestration.json），此处不再重复落盘
    print(json.dumps({"root_job_id": record.get("root_job_id"),
                      "draft_level": record.get("draft_level"),
                      "termination_reason": record.get("termination_reason", ""),
                      "message": record.get("message", ""),
                      "degraded": record.get("degraded", False),
                      "process_record": str(job_dir / "orchestration.json")
                      if job_dir.is_dir() else "",
                      "orchestration_plan": plan.as_dict(),
                      "plan_meta": meta},
                     ensure_ascii=False, indent=2))
    return 0 if record.get("termination_reason") == "success" else 1


def main():
    parser = argparse.ArgumentParser(description="运行任务并保存根任务账本")
    parser.add_argument("task", nargs="?")
    parser.add_argument("--mode", choices=("mock", "real"), default="mock")
    parser.add_argument("--profile")
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    # Q3-02/O-04 校准（2026-09-20 用户拍板，见 docs/BUDGET_CALIBRATION.md）：
    # 300→600（Q2-01 p95=369s 超旧默认 23%）；费用兜底 0.15（原 None=真实模式不传参不封顶）
    parser.add_argument("--max-seconds", type=float, default=600)
    parser.add_argument("--max-cost", type=float, default=0.15)
    parser.add_argument("--workspace")
    parser.add_argument("--import-file", action="append", default=[],
                        help="本地 TXT/Markdown 资料文件路径，可重复；只读原文")
    parser.add_argument("--import-text", action="append", default=[],
                        help="粘贴文本资料，可重复")
    parser.add_argument("--import-url", action="append", default=[],
                        help="用户指定的 http(s) 网页链接，可重复；按默认安全策略抓取正文")
    parser.add_argument("--allow-network", action="store_true",
                        help="声明允许联网研究；配置 SEARCH_PROVIDER=bing_scrape 后在真实模式自动搜索")
    parser.add_argument("--flow", choices=("agent", "research"), default="agent",
                        help="agent=通用Agent循环（默认）；research=资料整理/研究写作链（需资料）")
    parser.add_argument("--resume-job",
                        help="对指定研究写作任务续跑（按检查点跳过已完成阶段，需 --workspace）")
    parser.add_argument("--revise-job",
                        help="对指定任务追问改稿：以最新报告为原稿续用同批资料（需 --revise-text 与 --workspace）")
    parser.add_argument("--revise-text",
                        help="改稿指令文本（配合 --revise-job）")
    parser.add_argument("--require-section", action="append", default=[],
                        help="任务要求的必需章节名，可重复；研究写作链在程序层复验，缺失不算验收通过")
    parser.add_argument("--forbid-claim", action="append", default=[],
                        help="正文中不得出现的表述，可重复；S8-C 起程序层只提示疑似命中，判定交评测/人工")
    parser.add_argument("--key-fact", action="append", default=[],
                        help="任务要求覆盖的关键事实，可重复；未逐字覆盖记 warn（不阻塞验收）")
    parser.add_argument("--orchestration", choices=("auto", "single", "fixed", "manager_worker", "fanout", "dynamic_team", "debate"), default="auto",
                        help="执行方式（仅 --flow research 生效，S8 首版）：auto=调度智能体在 "
                             "fixed/fanout 间选型；fixed=固定研究链；fanout=拆子题并行研究后成稿。"
                             "选型失败自动降级 fixed；调度调用在真实模式计一次模型调用")
    parser.add_argument("--plan-only", action="store_true",
                        help="只输出执行方案（模式/理由/子任务/预算）不执行，配合 --orchestration")
    args = vars(parser.parse_args())
    workspace = args.pop("workspace")
    resume_job = args.pop("resume_job")
    revise_job = args.pop("revise_job")
    revise_text = args.pop("revise_text")
    task = args.pop("task")
    orchestration = args.pop("orchestration")
    plan_only = args.pop("plan_only")
    args["orchestration"] = orchestration   # D1-01：方式进入统一请求快照
    args["required_sections"] = tuple(args.pop("require_section"))
    args["forbidden_claims"] = tuple(args.pop("forbid_claim"))
    args["key_facts"] = tuple(args.pop("key_fact"))
    try:
        if resume_job:
            if not workspace:
                parser.error("--resume-job 需要 --workspace 指向任务所在工作区")
            result = resume_research_job(workspace_root=workspace, job_id=resume_job)
            raise SystemExit(_print_result(result, workspace=workspace))
        if revise_job:
            if not workspace:
                parser.error("--revise-job 需要 --workspace 指向任务所在工作区")
            if not revise_text:
                parser.error("--revise-job 需要 --revise-text 改稿指令")
            result = follow_up_revision(workspace_root=workspace, job_id=revise_job,
                                        instruction=revise_text)
            raise SystemExit(_print_result(result, workspace=workspace))
        args["texts"] = tuple(args.pop("import_text"))
        args["files"] = tuple(args.pop("import_file"))
        args["urls"] = tuple(args.pop("import_url"))
        if task is None:
            parser.error("需要提供任务文本（或使用 --resume-job）")
        try:
            request = TaskRequest(task=task, **args)
        except ValueError as e:
            parser.error(str(e))
        if request.flow == "research" and request.orchestration != "fixed":
            raise SystemExit(_run_orchestrated(request, workspace=workspace,
                                               plan_only=plan_only))
        app = ResearchApplication(request, workspace_root=workspace)
        result = app.run()
        raise SystemExit(_print_result(result, workspace=workspace))
    except ModelConfigError as e:
        parser.error(str(e))
    except ValueError as e:
        print(json.dumps({"status": "failed", "error_type": type(e).__name__,
                          "message": str(e)}, ensure_ascii=False))
        raise SystemExit(1) from None
    except Exception as e:
        print(json.dumps({"status": "failed", "error_type": type(e).__name__,
                          "message": str(e)}, ensure_ascii=False))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
