# -*- coding: utf-8 -*-
"""临时脚本：用合成任务数据启动工作台，逐视图截图并做交互自检。不接触真实 workspaces。"""
import json
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / "src" / "interfaces").is_dir())
sys.path.insert(0, str(ROOT))

from src.harness.run_store import write_json
from src.harness.state.db import StateDb
from src.harness.state.pending_inputs import PendingInputStore
from src.harness.state.queue import JobQueue
from src.harness.storage.artifacts import ArtifactStore
from src.harness.storage.sources import SourceStore
from src.interfaces.web.workbench import make_server

SCRATCH = ROOT / ".tmp"
SCRATCH.mkdir(parents=True, exist_ok=True)
WS = Path(tempfile.mkdtemp(prefix="ui_probe_", dir=str(SCRATCH)))
SHOTS = HERE
SHOTS.mkdir(parents=True, exist_ok=True)
JOB_A = "job_" + "a" * 32     # 已交付（accepted）
JOB_B = "job_" + "b" * 32     # 失败
JOB_C = "job_" + "c" * 32     # 待补充输入
RUN_ID = "20260922_120000_abc123"

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}/"


def seed():
    db = StateDb(WS / "state.sqlite")
    queue = JobQueue(db)
    pending = PendingInputStore(db)
    base = {"mode": "real", "flow": "research", "orchestration": "auto",
            "delivery_kind": "report", "max_calls": 12, "max_output_tokens": 8192,
            "max_seconds": 600, "max_cost": 0.15, "allow_network": True}

    # A：已交付 accepted
    a = WS / "jobs" / JOB_A
    (a / "artifacts").mkdir(parents=True, exist_ok=True)
    write_json(a / "request.json", dict(base, task="整理三份周报，写一份带引用的月度综述报告"))
    report = ("# 月度综述：Agentic RAG 的落地路径\n\n"
              "## 一、结论摘要\n本月的三份周报都指向同一方向：先做证据可核查的检索层[E-001]。\n\n"
              "## 二、依据与分歧\n- 检索质量是第一瓶颈[E-001]\n- 评测口径尚未统一[E-002]\n\n"
              "## 三、局限\n样本仅覆盖 8 个内部场景[E-003]\n")
    ArtifactStore(a).save("report", report, producer="draft")
    ArtifactStore(a).save("evidence", '{"items": 3}', ext="json", producer="evidence")
    write_json(a / "evidence.json", {"schema_version": 1, "items": [
        {"evidence_id": "E-001", "fact": "检索层的证据可核查性决定最终报告可信度",
         "tag": "support", "quote": "第 2 周周报原文：把引用能点回原文作为上线门槛。",
         "locator": {"paragraph": 3}, "source_id": "src_ab12cd", "note": "三份周报一致"},
        {"evidence_id": "E-002", "fact": "评测口径尚未统一", "tag": "conflict",
         "quote": "第 3 周周报原文：两个团队用了不同的命中率定义。",
         "locator": {"paragraph": 5}, "source_id": "src_ef34gh", "note": "冲突保留归属"},
        {"evidence_id": "E-003", "fact": "样本覆盖 8 个内部场景", "tag": "limit",
         "quote": "第 1 周周报原文：试点共 8 个场景。", "locator": {"paragraph": 2},
         "source_id": "src_ef34gh", "note": "范围有限"},
    ]})
    store = SourceStore(a)
    store.add_paste("第 2 周周报：把引用能点回原文作为上线门槛。", display_index=1)
    store.add_paste("第 3 周周报：两个团队用了不同的命中率定义。", display_index=2)
    write_json(a / "job.json", {"schema_version": 1, "root_job_id": JOB_A,
                                "status": "completed", "mode": "real",
                                "model": "research-pro-sim", "run_ids": [RUN_ID],
                                "error": None, "stop_reason": None,
                                "business_acceptance": "not_evaluated"})
    write_json(a / "ledger.json", {"schema_version": 1, "root_job_id": JOB_A,
                                   "status": "finished", "call_count": 9,
                                   "prompt_tokens": 24110, "output_tokens": 5120,
                                   "estimated_cost_usd": 0.0413,
                                   "unknown_usage_calls": 0, "elapsed_seconds": 142.5,
                                   "stop_reason": None})
    write_json(a / "pipeline.json", {"schema_version": 1, "root_job_id": JOB_A,
        "goal": "整理三份周报，写一份带引用的月度综述报告",
        "result": {"draft_level": "accepted", "termination_reason": "success",
                   "delivery_kind": "report", "revised_rounds": 1,
                   "total_citations": 3, "unresolved_citations": 0,
                   "hard_checks": {"required_sections_total": 2, "required_section_hits": 2,
                                   "forbidden_hits": 0, "fact_total": 1, "fact_hits": 1},
                   "stages": [
                       {"stage": "evidence", "status": "completed", "message": "提取 3 条证据"},
                       {"stage": "material", "status": "completed", "message": "素材包与冲突清单"},
                       {"stage": "outline", "status": "completed", "message": "提纲 3 节"},
                       {"stage": "draft", "status": "completed", "message": "初稿 1 版"},
                       {"stage": "review", "status": "completed", "message": "审校通过"},
                       {"stage": "finish", "status": "completed", "message": "accepted"}],
                   "message": "交付验收通过"}})

    # B：失败
    b = WS / "jobs" / JOB_B
    b.mkdir(parents=True, exist_ok=True)
    write_json(b / "request.json", dict(base, task="比较 A 方案与 B 方案的迁移成本"))
    write_json(b / "job.json", {"schema_version": 1, "root_job_id": JOB_B,
                               "status": "failed", "mode": "real", "model": "research-pro-sim",
                               "error": "StageError", "stop_reason": None})
    write_json(b / "pipeline.json", {"schema_version": 1, "root_job_id": JOB_B,
        "result": {"draft_level": "failed", "termination_reason": "error",
                   "stages": [{"stage": "evidence", "status": "failed",
                               "message": "证据提取输出不是合法 JSON 对象（items 列表缺失）"}],
                   "hard_checks": {}, "message": "阶段 evidence 失败"}})

    # C：待补充输入
    c = WS / "jobs" / JOB_C
    c.mkdir(parents=True, exist_ok=True)
    write_json(c / "request.json", dict(base, task="比较不同方案"))
    write_json(c / "input_request.json", {"schema_version": 1, "goal": "比较不同方案",
                                          "needs_input": True,
                                          "questions": ["请说明要比较的具体对象或范围。"]})

    for job_id, status, message, request in (
            (JOB_A, "completed", "研究链完成：accepted", dict(base, task="整理三份周报，写一份带引用的月度综述报告")),
            (JOB_B, "failed", "阶段 evidence 失败：证据提取输出不是合法 JSON 对象",
             dict(base, task="比较 A 方案与 B 方案的迁移成本")),
            (JOB_C, "waiting_input", "等待补充关键条件", dict(base, task="比较不同方案"))):
        queue.submit(job_id=job_id, kind="research", request=request, stage="finished")
        with db.write_tx() as conn:
            conn.execute("UPDATE jobs SET status=?, stage=?, message=? WHERE job_id=?",
                         (status, "finished" if status != "waiting_input" else "waiting_input",
                          message, job_id))
    pending.create(job_id=JOB_C, questions=["请说明要比较的具体对象或范围。"],
                   target="比较不同方案",
                   params=dict(base, task="比较不同方案"),
                   budget={"max_calls": 12, "max_cost": 0.15, "max_seconds": 600})

    # 通用 Agent 运行（观测视图）
    run = WS / RUN_ID
    run.mkdir(parents=True, exist_ok=True)
    write_json(run / "run.json", {"run_id": RUN_ID, "status": "completed",
                                  "mode": "real", "model": "agent-sim",
                                  "final_text": "27*43 = 1161", "root_job_id": JOB_A})
    lines = [
        {"event_id": "e1", "timestamp": "2026-09-22T12:00:01", "type": "run_start",
         "node": "agent", "run_id": RUN_ID},
        {"event_id": "e2", "timestamp": "2026-09-22T12:00:02", "type": "llm_call",
         "node": "agent", "model": "agent-sim", "round": 1, "run_id": RUN_ID},
        {"event_id": "e3", "timestamp": "2026-09-22T12:00:03", "type": "tool_call",
         "node": "tools", "name": "calculator", "arguments": {"expression": "27*43"},
         "run_id": RUN_ID},
        {"event_id": "e4", "timestamp": "2026-09-22T12:00:03", "type": "tool_result",
         "node": "tools", "name": "calculator", "result": "27*43 = 1161", "run_id": RUN_ID},
    ]
    (run / "trace.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines),
                                     encoding="utf-8")
    write_json(run / "job.json", {"schema_version": 1, "root_job_id": JOB_A,
                                  "status": "completed", "final_text": "27*43 = 1161"})
    write_json(WS / "jobs" / JOB_A / "ledger.json",
               {"schema_version": 1, "root_job_id": JOB_A, "status": "finished",
                "call_count": 9, "prompt_tokens": 24110, "output_tokens": 5120,
                "estimated_cost_usd": 0.0413, "unknown_usage_calls": 0,
                "elapsed_seconds": 142.5, "stop_reason": None})


def main():
    seed()
    server = make_server(workspaces=WS, port=PORT)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    from playwright.sync_api import sync_playwright
    problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1040})
        page.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
        page.on("console", lambda m: problems.append("console." + m.type + ": " + m.text)
                if m.type == "error" else None)
        page.on("response", lambda r: problems.append(f"http {r.status} {r.url}")
                if r.status >= 400 else None)
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(800)
        page.screenshot(path=str(SHOTS / "01-newtask.png"), full_page=True)

        page.click('a[href="#myJobs"]')
        page.wait_for_timeout(1200)
        page.screenshot(path=str(SHOTS / "02-jobs.png"), full_page=True)

        page.locator("#jobtbl tbody tr").filter(has_text="月度综述").first.click()
        page.wait_for_timeout(1500)
        page.screenshot(path=str(SHOTS / "03-job-accepted.png"), full_page=True)

        page.click('a[href="#newTask"]')
        page.wait_for_timeout(300)
        page.check("#allowNetwork")
        page.check("#planOnly")
        page.click("#start")
        page.wait_for_timeout(1500)
        banner = page.inner_text("#startNote")
        page.screenshot(path=str(SHOTS / "04-plan.png"), full_page=True)

        page.click('a[href="#myJobs"]')
        page.wait_for_timeout(600)
        page.locator("#jobtbl tbody tr").filter(has_text="待补充").first.click()
        page.wait_for_timeout(1400)
        asks = page.inner_text("#jobasks")
        page.screenshot(path=str(SHOTS / "05-waiting-input.png"), full_page=True)

        # 补充输入 → 应回到队列（真实模型未配置 → 由 worker 收敛为 failed，而不是卡在待补充）
        page.fill("#asktext", "比较对象是方案 A 与方案 B")
        page.uncheck("#askAppend")
        page.click("#jobasks button")
        page.wait_for_timeout(2500)
        page.screenshot(path=str(SHOTS / "05b-after-input.png"), full_page=True)
        after_input = page.inner_text("#joblog")

        page.click('a[href="#observe"]')
        page.wait_for_timeout(300)
        page.click("#runs button")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(SHOTS / "06-observe.png"), full_page=True)

        page.click('a[href="#statsDiag"]')
        page.wait_for_timeout(1600)
        page.screenshot(path=str(SHOTS / "07-stats.png"), full_page=True)
        diag = page.inner_text("#configDiag")
        stats = page.inner_text("#statsBody")

        # 真正提交一次（Mock + 研究写作链）：应自动跳到"任务与成果"并选中新任务
        page.click('a[href="#newTask"]')
        page.wait_for_timeout(300)
        page.uncheck("#planOnly")
        page.uncheck("#allowNetwork")
        page.fill("#task", "把粘贴的材料整理成一份说明")
        page.click("details.advanced summary")   # 高级设置（资料来源）默认折叠，先展开
        page.wait_for_timeout(200)
        page.fill("#pastetext", "材料一：结论 A。")
        page.click("#start")
        page.wait_for_timeout(2500)
        submit_note = page.inner_text("#startNote")
        selected = page.inner_text("#curjob")
        page.screenshot(path=str(SHOTS / "08-after-submit.png"), full_page=True)
        page.goto(f"{BASE}api/jobs/{JOB_A}/artifacts/report.v1/content", wait_until="load")
        raw_report = page.inner_text("body")
        browser.close()
    server.shutdown()
    server.server_close()

    out = ["PLAN_BANNER: " + banner.replace("\n", " | ")[:500],
           "REPORT_RAW: " + raw_report[:220].replace("\n", "\\n"),
           "ASKS: " + asks.replace("\n", " | ")[:300],
           "AFTER_INPUT: " + after_input.replace("\n", " | ")[:300],
           "DIAG: " + diag.replace("\n", " | ")[:400],
           "STATS: " + stats.replace("\n", " | ")[:300],
           "SUBMIT_NOTE: " + submit_note.replace("\n", " | ")[:200],
           "SELECTED_JOB: " + selected.replace("\n", " | ")[:80],
           "PROBLEMS: " + (str(problems) if problems else "none")]
    (SHOTS / "probe_result.txt").write_text("\n".join(out), encoding="utf-8")
    print("written:", SHOTS / "probe_result.txt")


if __name__ == "__main__":
    main()
