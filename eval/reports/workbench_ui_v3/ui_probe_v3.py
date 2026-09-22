# -*- coding: utf-8 -*-
"""临时脚本：用合成任务数据启动工作台（V3 界面），逐页截图并做交互自检。不接触真实 workspaces。"""
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
WS = Path(tempfile.mkdtemp(prefix="ui_v3_", dir=str(SCRATCH)))
SHOTS = HERE
JOB_A = "job_" + "a" * 32     # 已交付（accepted）
JOB_B = "job_" + "b" * 32     # 失败
JOB_C = "job_" + "c" * 32     # 待补充输入
RUN_ID = "20260922_120000_abc123"
PORT = 8801
BASE = f"http://127.0.0.1:{PORT}/"


def seed():
    db = StateDb(WS / "state.sqlite")
    queue = JobQueue(db)
    pending = PendingInputStore(db)
    base = {"mode": "mock", "flow": "research", "orchestration": "auto",
            "delivery_kind": "report", "max_calls": 12, "max_output_tokens": 8192,
            "max_seconds": 600, "max_cost": 0.15, "allow_network": True}

    a = WS / "jobs" / JOB_A
    (a / "artifacts").mkdir(parents=True, exist_ok=True)
    task_a = "整理三份周报，写一份带引用的月度综述报告"
    write_json(a / "request.json", dict(base, task=task_a))
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
                                "status": "completed", "mode": "mock",
                                "model": "mock-rule-v1", "run_ids": [RUN_ID],
                                "error": None, "stop_reason": None,
                                "business_acceptance": "not_evaluated"})
    write_json(a / "ledger.json", {"schema_version": 1, "root_job_id": JOB_A,
                                   "status": "finished", "call_count": 9,
                                   "prompt_tokens": 24110, "output_tokens": 5120,
                                   "estimated_cost_usd": 0.0413,
                                   "unknown_usage_calls": 0, "elapsed_seconds": 142.5,
                                   "stop_reason": None})
    write_json(a / "pipeline.json", {"schema_version": 1, "root_job_id": JOB_A,
        "goal": task_a,
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
    write_json(a / "orchestration.json", {"root_job_id": JOB_A, "status": "finished",
                                          "selected_mode": "manager_worker",
                                          "reason": "任务存在整理到成稿的依赖链，由统筹规划顺序派工",
                                          "tasks": [{"id": "T1", "role": "researcher", "status": "completed"},
                                                    {"id": "T2", "role": "writer", "status": "completed"}]})

    b = WS / "jobs" / JOB_B
    b.mkdir(parents=True, exist_ok=True)
    write_json(b / "request.json", dict(base, task="比较 A 方案与 B 方案的迁移成本"))
    write_json(b / "job.json", {"schema_version": 1, "root_job_id": JOB_B,
                               "status": "failed", "mode": "mock", "model": "mock-rule-v1",
                               "error": "StageError", "stop_reason": None})
    write_json(b / "pipeline.json", {"schema_version": 1, "root_job_id": JOB_B,
        "result": {"draft_level": "failed", "termination_reason": "error",
                   "stages": [{"stage": "evidence", "status": "failed",
                               "message": "证据提取输出不是合法 JSON 对象（items 列表缺失）"}],
                   "hard_checks": {}, "message": "阶段 evidence 失败"}})

    c = WS / "jobs" / JOB_C
    c.mkdir(parents=True, exist_ok=True)
    write_json(c / "request.json", dict(base, task="比较不同方案", texts=["材料一：结论 A。"]))
    write_json(c / "input_request.json", {"schema_version": 1, "goal": "比较不同方案",
                                          "needs_input": True,
                                          "questions": ["请说明要比较的具体对象或范围。"]})

    for job_id, status, message, request, stage in (
            (JOB_A, "completed", "研究链完成：accepted", dict(base, task=task_a), "finished"),
            (JOB_B, "failed", "阶段 evidence 失败：证据提取输出不是合法 JSON 对象",
             dict(base, task="比较 A 方案与 B 方案的迁移成本"), "finished"),
            (JOB_C, "waiting_input", "等待补充关键条件", dict(base, task="比较不同方案"), "waiting_input")):
        queue.submit(job_id=job_id, kind="research", request=request, stage=stage)
        with db.write_tx() as conn:
            conn.execute("UPDATE jobs SET status=?, stage=?, message=? WHERE job_id=?",
                         (status, stage, message, job_id))
    pending.create(job_id=JOB_C, questions=["请说明要比较的具体对象或范围。"],
                   target="比较不同方案",
                   params=dict(base, task="比较不同方案", texts=["材料一：结论 A。"]),
                   budget={"max_calls": 12, "max_cost": 0.15, "max_seconds": 600})

    run = WS / RUN_ID
    run.mkdir(parents=True, exist_ok=True)
    write_json(run / "run.json", {"run_id": RUN_ID, "status": "completed",
                                  "mode": "mock", "model": "mock-rule-v1",
                                  "final_text": "27*43 = 1161", "root_job_id": JOB_A})
    lines = [
        {"event_id": "e1", "timestamp": "2026-09-22T12:00:01", "type": "run_start",
         "node": "agent", "run_id": RUN_ID},
        {"event_id": "e2", "timestamp": "2026-09-22T12:00:02", "type": "llm_call",
         "node": "agent", "model": "mock-rule-v1", "round": 1, "run_id": RUN_ID},
        {"event_id": "e3", "timestamp": "2026-09-22T12:00:03", "type": "tool_call",
         "node": "tools", "name": "calculator", "arguments": {"expression": "27*43"},
         "run_id": RUN_ID},
        {"event_id": "e4", "timestamp": "2026-09-22T12:00:04", "type": "tool_result",
         "node": "tools", "name": "calculator", "result": "27*43 = 1161", "run_id": RUN_ID},
    ]
    (run / "trace.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines),
                                     encoding="utf-8")
    write_json(run / "job.json", {"schema_version": 1, "root_job_id": JOB_A,
                                  "status": "completed", "final_text": "27*43 = 1161"})


def main():
    seed()
    server = make_server(workspaces=WS, port=PORT)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    from playwright.sync_api import sync_playwright
    problems, notes = [], {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
        page.on("console", lambda m: problems.append("console.error: " + m.text)
                if m.type == "error" else None)
        page.on("response", lambda r: problems.append(f"http {r.status} {r.url}")
                if r.status >= 400 else None)

        # 首页
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(900)
        page.screenshot(path=str(SHOTS / "01-home.png"), full_page=True)
        notes["home_recent"] = page.inner_text("#recentJobs")[:120].replace("\n", " | ")
        notes["start_enabled"] = str(page.is_enabled("#start"))

        # 示例任务 + 高级设置抽屉
        page.click(".example >> nth=1")
        notes["example"] = page.input_value("#taskEditor")[:60]
        page.click("#sourceBtn")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "02-drawer.png"), full_page=True)
        notes["drawer_mode"] = page.input_value("#mode")
        page.click("#advancedDrawer .advanced-actions button")
        page.wait_for_timeout(300)

        # 先看计划（不执行）
        page.click("#planBtn")
        page.fill("#taskEditor", "整理三份周报，写一份带引用的月度综述报告")
        page.click("#start")
        page.wait_for_timeout(1500)
        notes["plan"] = page.inner_text("#planPreview")[:180].replace("\n", " | ")
        page.screenshot(path=str(SHOTS / "03-plan.png"), full_page=True)
        page.click("#planPreview button.btn")   # 关闭
        page.click("#planBtn")                  # 关掉"先看计划"
        page.wait_for_timeout(200)

        # 任务中心：筛选 + 搜索
        page.click('a[data-nav="tasks"]')
        page.wait_for_timeout(900)
        page.screenshot(path=str(SHOTS / "04-tasks.png"), full_page=True)
        notes["tasks"] = page.inner_text("#jobtbl")[:160].replace("\n", " | ")
        page.click('[data-filter="attention"]')
        page.wait_for_timeout(300)
        notes["tasks_attention"] = page.inner_text("#jobtbl")[:80].replace("\n", " | ")
        page.click('[data-filter="all"]')
        page.wait_for_timeout(300)

        # 任务详情：结果（点击引用看证据）
        page.locator("#jobtbl .task-cell").filter(has_text="月度综述").first.click()
        page.wait_for_timeout(1600)
        page.screenshot(path=str(SHOTS / "05-job-result.png"), full_page=True)
        notes["summary"] = page.inner_text(".summary-strip").replace("\n", " | ")[:200]
        page.click("#rtok .citation >> nth=0")
        page.wait_for_timeout(300)
        notes["evidence"] = page.inner_text("#docview").replace("\n", " | ")[:160]
        page.screenshot(path=str(SHOTS / "06-evidence.png"), full_page=True)

        # 资料与产物
        page.click('[data-jobtab="sources"]')
        page.wait_for_timeout(400)
        page.locator("#srclist .source-item").first.click()
        page.wait_for_timeout(600)
        notes["source_view"] = page.inner_text("#sourceViewer")[:80].replace("\n", " | ")
        page.screenshot(path=str(SHOTS / "07-sources.png"), full_page=True)

        # 执行过程 + 版本与改稿
        page.click('[data-jobtab="process"]')
        page.wait_for_timeout(400)
        notes["process"] = page.inner_text("#processMode")[:80]
        page.screenshot(path=str(SHOTS / "08-process.png"), full_page=True)
        page.click('[data-jobtab="versions"]')
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "09-versions.png"), full_page=True)

        # 待补充任务：补充后应回队
        page.click('a[data-nav="tasks"]')
        page.wait_for_timeout(600)
        page.locator("#jobtbl .task-cell").filter(has_text="比较不同方案").first.click()
        page.wait_for_timeout(1400)
        notes["ask"] = page.inner_text("#jobasks")[:120].replace("\n", " | ")
        page.screenshot(path=str(SHOTS / "10-waiting-input.png"), full_page=True)
        page.fill("#asktext", "比较对象是方案 A 与方案 B")
        page.click("#jobasks button")
        page.wait_for_timeout(1800)
        notes["after_input"] = page.inner_text("#joblog").replace("\n", " | ")[:120]

        # 真实提交一次（Mock 模式，研究写作链）→ 应跳到任务详情
        page.click('a[data-nav="home"]')
        page.wait_for_timeout(400)
        page.click("#planBtn")  # 打开"先看计划"再关掉前先确认状态
        page.click("#planBtn")
        page.click("#sourceBtn")
        page.wait_for_timeout(300)
        page.select_option("#mode", "mock")
        page.fill("#pastetext", "材料一：结论 A。")
        page.click("#advancedDrawer .advanced-actions button")
        page.wait_for_timeout(200)
        page.fill("#taskEditor", "把粘贴的材料整理成一份说明")
        page.click("#start")
        page.wait_for_timeout(2500)
        notes["submitted_url"] = page.url
        notes["submitted_title"] = page.inner_text("#jobTitle")[:60]
        page.screenshot(path=str(SHOTS / "11-submitted.png"), full_page=True)

        # 运行观测 + 配置与评测
        page.click('a[data-nav="observe"]')
        page.wait_for_timeout(700)
        page.locator("#runs .run-item").first.click()
        page.wait_for_timeout(1500)
        page.screenshot(path=str(SHOTS / "12-observe.png"), full_page=True)
        page.click('a[data-nav="settings"]')
        page.wait_for_timeout(1400)
        notes["settings"] = page.inner_text("#configDiag").replace("\n", " | ")[:160]
        notes["stats"] = page.inner_text("#statsBody").replace("\n", " | ")[:120]
        notes["boundary"] = page.inner_text(".settings-grid .card.wide >> nth=1")[:120].replace("\n", " | ")
        page.screenshot(path=str(SHOTS / "13-settings.png"), full_page=True)
        browser.close()
    server.shutdown()
    server.server_close()

    out = [f"{k}: {v}" for k, v in notes.items()]
    out.append("PROBLEMS: " + (str(problems) if problems else "none"))
    (SHOTS / "probe_result.txt").write_text("\n".join(out), encoding="utf-8")
    print("written:", SHOTS / "probe_result.txt")


if __name__ == "__main__":
    main()
