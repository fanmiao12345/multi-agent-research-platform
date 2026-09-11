# -*- coding: utf-8 -*-
"""S8-01/02/03 离线测试：执行方案契约、护栏与预算分配、调度智能体、执行器接线。"""
import pytest

from src.application.orchestration import (
    Budget,
    OrchestrationExecutor,
    OrchestrationGuards,
    OrchestrationScheduler,
    PlanValidationError,
    allocate_budget,
    compute_dispatchable_pool,
    final_reserve_of,
    from_plan_dict,
    heuristic_plan,
)
from src.application.orchestration.guards import GuardViolation
from src.application.request import TaskRequest

CAPS = Budget(max_calls=40, max_cost_usd=0.30, max_seconds=900)


# ---- 契约 -------------------------------------------------------------------
def _fanout_plan_dict(**over):
    data = {
        "schema_version": "1", "mode": "fanout",
        "reason": "两个独立子题，可并行",
        "complexity_signals": {"independent_subtopics": 2},
        "subtasks": [
            {"id": "T1", "role": "researcher", "description": "子题一", "depends_on": [],
             "parallel": True, "covers_sections": []},
            {"id": "T2", "role": "researcher", "description": "子题二", "depends_on": [],
             "parallel": True, "covers_sections": []},
            {"id": "T3", "role": "writer", "description": "成稿", "depends_on": ["T1", "T2"],
             "parallel": False, "covers_sections": ["资料目录"]},
        ],
        "needs_reviewer": True, "max_parallel": 3,
        "budget": {"max_calls": 30, "max_cost_usd": 0.20, "max_seconds": 600},
        "fallback_mode": "fixed",
        "expected": {"calls": 12, "cost_usd": 0.1, "seconds": 300},
    }
    data.update(over)
    return data


def test_contract_accepts_valid_plan_and_rejects_illegal_ones():
    plan = from_plan_dict(_fanout_plan_dict())
    assert plan.mode == "fanout" and len(plan.subtasks) == 3
    assert plan.budget.max_cost_usd == 0.20

    bad_cases = [
        ("mode 必须取已开放模式", _fanout_plan_dict(mode="debate")),
        ("role 必须取", _fanout_plan_dict() | {"subtasks": [
            {"id": "T1", "role": "wizard", "description": "x"}]}),
        ("依赖未定义", _fanout_plan_dict() | {"subtasks": [
            {"id": "T1", "role": "writer", "description": "x", "depends_on": ["T9"]}]}),
        ("依赖自身", _fanout_plan_dict() | {"subtasks": [
            {"id": "T1", "role": "writer", "description": "x", "depends_on": ["T1"]}]}),
        ("max_parallel 必须为", _fanout_plan_dict(max_parallel=9)),
        ("max_calls 必须为非负整数",
         _fanout_plan_dict(budget={"max_calls": -1, "max_cost_usd": 0, "max_seconds": 0})),
        ("reason 必须为非空文本", _fanout_plan_dict(reason="  ")),
        ("schema_version 必须为", _fanout_plan_dict(schema_version="2")),
    ]
    for match_text, data in bad_cases:
        with pytest.raises(PlanValidationError):
            from_plan_dict(data)


def test_first_version_mode_catalog_is_filtered():
    with pytest.raises(PlanValidationError):
        from_plan_dict(_fanout_plan_dict(mode="debate"))  # 未开放模式必须被过滤


# ---- 预算分配（S8-03 公式）---------------------------------------------------
def test_budget_clamp_subtract_and_pool():
    assert Budget(max_calls=99, max_cost_usd=9.0, max_seconds=9999).clamp_to(CAPS) == CAPS
    reserve = final_reserve_of(CAPS)
    assert reserve.max_calls == 16 and abs(reserve.max_cost_usd - 0.12) < 1e-9  # 40% 预留
    pool = compute_dispatchable_pool(CAPS, final_reserve=reserve)
    assert pool.max_calls == 24 and abs(pool.max_cost_usd - 0.18) < 1e-9
    pool2 = compute_dispatchable_pool(CAPS, inflight=Budget(max_calls=4, max_cost_usd=0.02,
                                                            max_seconds=100),
                                      final_reserve=reserve)
    assert pool2.max_calls == 20 and abs(pool2.max_cost_usd - 0.16) < 1e-9  # 再扣在途


def test_allocate_budget_respects_single_cap_and_sum_bound():
    reserved_pool = compute_dispatchable_pool(CAPS, final_reserve=final_reserve_of(CAPS))
    shares = allocate_budget(reserved_pool, 1)
    assert shares[0].max_calls == int(reserved_pool.max_calls * 0.4)  # 单子任务 ≤ 池 40%
    shares3 = allocate_budget(reserved_pool, 3)
    assert sum(s.max_calls for s in shares3) <= reserved_pool.max_calls  # Σ ≤ 池（防3×40%）
    assert all(abs(s.max_cost_usd - reserved_pool.max_cost_usd / 3) < 1e-6
               for s in shares3)                                # n≥3 均分 ≤ 40% 上限
    assert allocate_budget(CAPS, 0) == []


# ---- 护栏 -------------------------------------------------------------------
def test_guards_dedup_count_and_depth():
    g = OrchestrationGuards(max_total_subtasks=2, max_depth=2)
    g.register_subtask("T1", "收集 A 题资料", depth=1)
    with pytest.raises(GuardViolation, match="同题重复派生"):
        g.register_subtask("T2", "收集 A 题资料！", depth=2)   # 换皮同题 → 拒绝
    g.register_subtask("T4", "另一件事", depth=2)              # 计数=2 达上限
    with pytest.raises(GuardViolation, match="总数"):
        g.register_subtask("T5", "再派一个", depth=2)
    with pytest.raises(GuardViolation, match="深度"):
        g.register_subtask("T6", "太深的孙辈", depth=3)


# ---- 调度智能体 ----------------------------------------------------------------
def test_heuristic_plan_is_deterministic_and_covers_sections():
    p1 = heuristic_plan("行业现状", complexity_signals={"independent_subtopics": 2},
                        required_sections=("资料目录",), budget_caps=CAPS)
    p2 = heuristic_plan("行业现状", complexity_signals={"independent_subtopics": 2},
                        required_sections=("资料目录",), budget_caps=CAPS)
    assert p1.as_dict() == p2.as_dict()                    # 桩大脑确定性
    assert p1.mode == "fanout" and p1.budget == CAPS
    covered = {c for st in p1.subtasks for c in st.covers_sections}
    assert {"资料目录"} <= covered
    single = heuristic_plan("单点整理", required_sections=(), budget_caps=CAPS)
    assert single.mode == "fixed"


class _FakeLLM:
    # D2-01：经根网关调用需可定价与用量统计——桩声明 mock 模式与已知模型名
    model_name = "pipeline-stub"
    run_mode = "mock"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply

        class _R:
            content = reply
            usage = {"prompt_tokens": 5, "completion_tokens": 7}
        return _R()


def test_scheduler_llm_path_clamps_budget_and_validates():
    data = _fanout_plan_dict(budget={"max_calls": 500, "max_cost_usd": 9.0,
                                     "max_seconds": 9999})
    llm = _FakeLLM(["前置说明\n```json\n" + __import__("json").dumps(data,
                                                             ensure_ascii=False) + "\n```"])
    plan, meta = OrchestrationScheduler(llm).plan(
        "主题", required_sections=("资料目录",), budget_caps=CAPS)
    assert meta["scheduler"] == "llm" and meta["attempts"] == 1
    assert plan.budget == CAPS                             # 程序钳制，不信任模型报价
    assert plan.mode == "fanout"


def test_scheduler_retries_missing_coverage_then_falls_back():
    bad = _fanout_plan_dict()                              # 先造一个缺覆盖的方案
    bad["subtasks"][2]["covers_sections"] = []
    good = _fanout_plan_dict()                             # T3 已覆盖"资料目录"
    import json as _json
    llm = _FakeLLM([_json.dumps(bad, ensure_ascii=False), _json.dumps(good,
                                                                     ensure_ascii=False)])
    plan, meta = OrchestrationScheduler(llm).plan("主题", required_sections=("资料目录",),
                                                  budget_caps=CAPS)
    assert meta["attempts"] == 2 and plan.mode == "fanout"
    assert llm.calls == 2

    llm2 = _FakeLLM(["不是JSON", "还不是JSON"])
    plan2, meta2 = OrchestrationScheduler(llm2).plan("主题", required_sections=(),
                                                     budget_caps=CAPS)
    assert meta2["scheduler"] == "fallback" and len(meta2["failures"]) == 2
    assert plan2.mode == "fixed" and "选型失败" in plan2.reason  # 降级并如实记录


# ---- 执行器（S8-02 接线）-------------------------------------------------------
class _Outcome:
    def __init__(self, job, text, level, reason="success"):
        self.root_job_id, self.final_text = job, text
        self.draft_level, self.termination_reason = level, reason


class _FakeApp:
    """记录请求与运行参数（job_id/父子关系）并按任务内容返回预设结果的假研究应用。"""

    def __init__(self, requests, script, runs):
        self.requests, self.script, self.runs = requests, script, runs

    def run(self, job_id=None, parent_job_id=None):
        req = self.requests[-1]
        self.runs.append({"job_id": job_id, "parent_job_id": parent_job_id,
                          "task": req.task})
        outcome = self.script(req)
        return _Outcome(outcome[0], outcome[1], outcome[2])


def _executor_with(script, workspace_root=None):
    """返回 (executor, requests, runs)；runs 汇总每次 run 的 job_id/父子参数。"""
    requests, runs = [], []

    def factory(workspace_root, settings, llm):
        def build(request):
            requests.append(request)
            return _FakeApp(requests, script, runs)
        return build
    executor = OrchestrationExecutor(workspace_root=workspace_root, app_factory=factory)
    return executor, requests, runs


def _req(**over):
    """D1-01：统一任务请求样例（执行器以 TaskRequest 为唯一事实来源）。"""
    fields = dict(task="研究主题", mode="mock", texts=("原始资料",),
                  max_cost=0.30, max_seconds=300)
    fields.update(over)
    return TaskRequest(**fields)


def test_executor_fixed_mode_passes_request_through_unchanged(tmp_path):
    req = _req(files=("a.md",), profile="p1", allow_network=True, max_cost=0)
    root = "job_" + "f" * 32
    ex, requests, runs = _executor_with(lambda r: ("job_f1", "报告", "accepted"),
                                        workspace_root=tmp_path)
    plan = heuristic_plan(req.task, budget_caps=CAPS)
    record = ex.execute_plan(req, plan, budget_caps=CAPS, root_job_id=root)
    assert record["draft_level"] == "accepted" and record["root_job_id"] == root
    assert not record["subtasks"] and not record["degraded"]
    assert record["orchestration"] == "auto"
    sent = requests[0]
    # D1-01 契约：fixed 原样透传——文件/显式模型/网络策略/零限额一个都不丢
    assert sent is req
    assert sent.files == ("a.md",) and sent.profile == "p1"
    assert sent.allow_network is True and sent.max_cost == 0
    # D1-02：根 job 即交付 job；根记录已落盘并含方案与最终状态
    assert runs[0]["job_id"] == root and runs[0]["parent_job_id"] is None
    import json
    rec = json.loads((tmp_path / "jobs" / root / "orchestration.json")
                     .read_text(encoding="utf-8"))
    assert rec["status"] == "finished" and rec["root_job_id"] == root
    assert rec["plan"]["mode"] == "fixed"


def test_executor_fanout_materializes_shared_sources_and_integrates_outputs(tmp_path):
    material = tmp_path / "a.md"
    material.write_text("# 文件资料\n\n共享文件正文。\n", encoding="utf-8")

    def script(req):
        if "子题一" in req.task:
            return ("job_s1", "子题一小节", "accepted")
        if "子题二" in req.task:
            return ("job_s2", "子题二小节", "accepted")
        return ("job_root", "最终报告", "accepted")

    req = _req(files=(str(material),), profile="p1", max_output_tokens=7777,
               allow_network=True, required_sections=("资料目录",))
    root = "job_" + "a" * 32
    ex, requests, runs = _executor_with(script, workspace_root=tmp_path)
    plan = from_plan_dict(_fanout_plan_dict())
    record = ex.execute_plan(req, plan, budget_caps=CAPS,
                             plan_meta={"scheduler": "heuristic"}, root_job_id=root)
    assert [s["id"] for s in record["subtasks"]] == ["T1", "T2"]   # writer 不做子运行
    assert record["draft_level"] == "accepted" and record["root_job_id"] == root
    root_req = requests[-1]
    integrated = "\n".join(root_req.texts)
    assert "原始资料" in integrated and "子题一小节" in integrated   # 子产出接入根任务来源
    assert "[子智能体 T1·researcher 产出" in integrated
    # D3-05：原始文件/URL只在根共享库读取一次，子任务和成稿复用正文。
    assert root_req.files == () and root_req.urls == ()
    assert root_req.profile == "p1"
    assert root_req.max_output_tokens == 7777 and root_req.allow_network is False
    assert root_req.required_sections == ("资料目录",)
    assert "共享文件正文" in integrated
    sub_req = requests[0]
    assert sub_req.profile == "p1" and sub_req.allow_network is False
    assert sub_req.files == () and sub_req.urls == ()
    assert sub_req.required_sections == ()                # 子任务不带最终交付的章节硬要求
    assert record["budget_split"]["pool"]["max_cost_usd"] < CAPS.max_cost_usd
    assert record["plan_meta"]["scheduler"] == "heuristic"
    # D1-02：子任务记 child/parent；根任务复用调度前预留的 job_id；根记录列出全部子任务
    assert runs[0]["parent_job_id"] == root and runs[0]["job_id"] is None
    assert runs[-1]["job_id"] == root and runs[-1]["parent_job_id"] is None
    child_jobs = [e["child_job_id"] for e in record["subtasks"]]
    assert child_jobs == ["job_s1", "job_s2"]
    assert all(e["parent_job_id"] == root for e in record["subtasks"])
    import json
    rec = json.loads((tmp_path / "jobs" / root / "orchestration.json")
                     .read_text(encoding="utf-8"))
    assert [c["child_job_id"] for c in rec["subtasks"]] == child_jobs
    assert rec["status"] == "finished"


def test_executor_subtask_retry_then_degrade_not_silent(tmp_path):
    calls = {"n": 0}

    def script(req):
        if "子题一" in req.task:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("子运行首次失败")        # 触发原地重试一次
            return ("job_s1", "子题一小节", "accepted")
        if "子题二" in req.task:
            return ("job_s2", "", "failed")                 # 重试后仍不达标
        return ("job_root", "最终报告", "draft")

    ex, requests, runs = _executor_with(script, workspace_root=tmp_path)
    plan = from_plan_dict(_fanout_plan_dict())
    record = ex.execute_plan(_req(texts=()), plan, budget_caps=CAPS)
    t1 = next(s for s in record["subtasks"] if s["id"] == "T1")
    t2 = next(s for s in record["subtasks"] if s["id"] == "T2")
    assert t1["attempts"] == 2 and not t1["failure"]        # 重试记为新尝试后成功
    assert t2["failure"] and "未达标" in t2["failure"]
    assert record["degraded"] is True                       # 不静默跳过：降级标记
    assert "子题二小节" not in "\n".join(requests[-1].texts)  # 失败产出不进根任务
    assert record["draft_level"] == "draft"


def test_final_job_json_adopts_orchestration_block(tmp_path):
    """D1-02：最终交付 job.json 并入方案、子任务与降级信息（一份根记录可查全貌）。"""
    import json
    root = "job_" + "b" * 32
    job_dir = tmp_path / "jobs" / root
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps(
        {"root_job_id": root, "status": "completed"}, ensure_ascii=False),
        encoding="utf-8")
    ex, requests, runs = _executor_with(lambda r: (
        ("job_s1", "子题一小节", "accepted") if "子题一" in r.task else
        ("job_s2", "子题二小节", "accepted") if "子题二" in r.task else
        (root, "最终报告", "accepted")), workspace_root=tmp_path)
    plan = from_plan_dict(_fanout_plan_dict())
    record = ex.execute_plan(_req(), plan, root_job_id=root)
    assert record["draft_level"] == "accepted"
    payload = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    block = payload["orchestration"]
    assert block["plan"]["mode"] == "fanout"
    assert [c["child_job_id"] for c in block["children"]] == ["job_s1", "job_s2"]
    assert block["children"][0]["parent_job_id"] == root


def test_structured_sub_result_collects_refs(tmp_path):
    """D1-03：子交付=结构化结果（证据/产物/来源引用可定位），不是一段裸文本。"""
    import json
    child = "job_" + "c" * 32
    job_dir = tmp_path / "jobs" / child
    (job_dir / "artifacts").mkdir(parents=True)
    (job_dir / "artifacts" / "report.v1.md").write_text("# 子报告", encoding="utf-8")
    (job_dir / "evidence.json").write_text(json.dumps({"items": [
        {"evidence_id": "E-001", "source_id": "s01", "quote": "试点共40人"},
        {"evidence_id": "E-002", "source_id": "s01", "quote": "每账号每月20元"}]},
        ensure_ascii=False), encoding="utf-8")
    (job_dir / "sources.json").write_text(json.dumps({"sources": [
        {"source_id": "s01", "title": "试点登记"}]}, ensure_ascii=False),
        encoding="utf-8")

    root = "job_" + "d" * 32
    ex, requests, runs = _executor_with(lambda r: (
        (child, "子题一小节", "accepted") if "子题一" in r.task else
        ("job_s2", "x", "accepted") if "子题二" in r.task else
        (root, "最终报告", "accepted")), workspace_root=tmp_path)
    plan = from_plan_dict(_fanout_plan_dict())
    record = ex.execute_plan(_req(), plan, root_job_id=root)
    result = record["subtasks"][0]["result"]
    assert result is not None and result["child_job_id"] == child
    assert [e["evidence_id"] for e in result["evidence_refs"]] == ["E-001", "E-002"]
    assert result["evidence_refs"][0]["quote_head"] == "试点共40人"
    assert result["artifact_refs"][0]["kind"] == "report"
    assert result["source_refs"][0]["source_id"] == "s01"
    assert result["summary"] == "子题一小节"
    assert result["truncation_note"] == ""
    # 根记录（orchestration.json）中也含结构化结果
    rec = json.loads((tmp_path / "jobs" / root / "orchestration.json")
                     .read_text(encoding="utf-8"))
    assert rec["subtasks"][0]["result"]["evidence_refs"][0]["evidence_id"] == "E-001"


def test_unified_contract_normalization():
    """D1-04：交付等级与执行状态两套词汇分离；未知值保守归一；统一返回形状固定。"""
    from src.application.orchestration import (DELIVERY_LEVELS, normalize_level,
                                               normalize_status, unified_record)
    assert "unable" in DELIVERY_LEVELS and "failed" in DELIVERY_LEVELS
    # 交付等级归一：未知/缺失保守落 failed
    assert normalize_level("accepted") == "accepted"
    assert normalize_level("unable") == "unable"
    assert normalize_level("whatever") == "failed" and normalize_level(None) == "failed"
    # 执行状态：能诚实拒绝（unable）是流程正常结束的一种，但不是"完成"
    assert normalize_status("success", "accepted") == "completed"
    assert normalize_status("unable", "unable") == "partial"
    assert normalize_status("budget_exceeded") == "cancelled"
    assert normalize_status("mystery") == "failed"
    assert normalize_status("success", "failed") == "partial"   # 结束了但没有可交付等级
    # 统一返回形状：任何入口收尾一致；旧字段读取用 .get 兼容
    r = unified_record(root_job_id="job_x", draft_level="unable",
                       termination_reason="unable", message="缺资料")
    assert r["delivery_label"] == "无法完成" and r["status"] == "partial"
    assert r.get("不存在的旧字段") is None


def test_executor_record_carries_unified_block(tmp_path):
    root = "job_" + "e" * 32
    ex, requests, runs = _executor_with(lambda r: (root, "报告", "accepted"),
                                        workspace_root=tmp_path)
    record = ex.execute_plan(_req(), heuristic_plan(_req().task, budget_caps=CAPS),
                             root_job_id=root)
    u = record["unified"]
    assert u["draft_level"] == "accepted" and u["delivery_label"] == "成品"
    assert u["status"] == "completed" and u["root_job_id"] == root


def test_scheduler_call_goes_through_root_ledger(tmp_path):
    """D2-01：调度调用经根网关记账（purpose/role 可查），续接账本不丢条目。"""
    import json
    from src.harness.model_gateway import JobLedger, job_scope
    root = "job_" + "1" * 32
    ledger = JobLedger(tmp_path / "jobs" / root, _req())
    data = _fanout_plan_dict()
    llm = _FakeLLM([json.dumps(data, ensure_ascii=False)])
    with job_scope(ledger):
        plan, meta = OrchestrationScheduler(llm).plan("主题", budget_caps=CAPS)
    assert plan.mode == "fanout"
    summary = ledger.summary()
    assert summary["call_count"] == 1
    entry = summary["calls"][0]
    assert entry["purpose"] == "orchestration_plan" and entry["role"] == "scheduler"
    ledger.finish("completed")
    ledger2 = JobLedger(tmp_path / "jobs" / root, _req())
    assert any(c["purpose"] == "orchestration_plan"
               for c in ledger2.summary()["calls"])   # 链阶段续接，不覆盖调度条目


def test_child_run_rollup_counts_cost_not_calls(tmp_path):
    """D2-01：子任务成本入根账本——child_run 不占调用次数，费用计入根预算。"""
    from src.harness.model_gateway import JobLedger
    root = "job_" + "2" * 32
    ledger = JobLedger(tmp_path / "jobs" / root, _req())
    ledger.record_child_run(child_job_id="job_c1", role="researcher",
                            draft_level="accepted", cost_usd=0.02, attempts=1)
    ledger.record_child_run(child_job_id="job_c2", role="researcher",
                            draft_level="draft", cost_usd=0.03, attempts=2)
    summary = ledger.summary()
    assert summary["call_count"] == 0
    assert abs(summary["known_estimated_cost_usd"] - 0.05) < 1e-9
    ledger.finish("completed")
    ledger2 = JobLedger(tmp_path / "jobs" / root, _req())
    assert len(ledger2.summary()["calls"]) == 2        # 续接不丢历史条目


def test_executor_records_child_cost(tmp_path):
    """执行器从子任务账本读实际费用入编排记录；无账本显式 None 不记零。"""
    import json
    child = "job_" + "c" * 32
    job_dir = tmp_path / "jobs" / child
    job_dir.mkdir(parents=True)
    (job_dir / "ledger.json").write_text(json.dumps({"estimated_cost_usd": 0.048}),
                                         encoding="utf-8")
    root = "job_" + "3" * 32
    ex, requests, runs = _executor_with(lambda r: (
        (child, "子题一小节", "accepted") if "子题一" in r.task else
        ("job_s2", "x", "accepted") if "子题二" in r.task else
        (root, "最终报告", "accepted")), workspace_root=tmp_path)
    record = ex.execute_plan(_req(), from_plan_dict(_fanout_plan_dict()),
                             root_job_id=root)
    assert record["subtasks"][0]["cost_usd"] == 0.048
    assert record["subtasks"][1]["cost_usd"] is None


def test_reserve_is_atomic_and_settlement_restores_capacity(tmp_path):
    """D2-02：两个并发申请不能超分；结算后容量恢复；失败不能重置预算。"""
    import json
    from src.harness.model_gateway import BudgetStop, JobLedger
    root = "job_" + "4" * 32
    ledger = JobLedger(tmp_path / "jobs" / root, _req(max_cost=0.10))
    r1 = ledger.reserve(0.06, purpose="child:T1", ref_id="T1")
    with pytest.raises(BudgetStop, match="预算预留失败"):
        ledger.reserve(0.06, purpose="child:T2", ref_id="T2")   # 0.06+0.06>0.10
    assert ledger.summary()["reserved_usd"] == 0.06
    # T1 实际花费 0.02 → 结算后容量恢复到 0.08，T2 可预留
    ledger.settle(r1, 0.02, child_job_id="job_c1", role="researcher",
                  draft_level="accepted")
    assert ledger.summary()["reserved_usd"] == 0.0
    r2 = ledger.reserve(0.06, purpose="child:T2", ref_id="T2")
    # T2 实际费用未知 → 按预留额保守入账且标记不完整
    ledger.settle(r2, None, child_job_id="job_c2", role="researcher")
    summary = ledger.summary()
    assert abs(summary["known_estimated_cost_usd"] - 0.08) < 1e-9   # 0.02+0.06(保守)
    conservative = [c for c in summary["calls"] if c.get("conservative")]
    assert conservative and conservative[0]["usage_complete"] is False
    # 预留记录可审计：状态机 reserved→settled/settled_unknown
    ledger3 = JobLedger(tmp_path / "jobs" / root, _req(max_cost=0.10))
    states = {r["ref_id"]: r["status"] for r in ledger3.reservations}
    assert states == {"T1": "settled", "T2": "settled_unknown"}


def test_zero_budget_reservation_rejected_without_request(tmp_path):
    """D2-02：零预算在预留时即拒绝——子任务一次请求都不发。"""
    from src.harness.model_gateway import BudgetStop, JobLedger
    root = "job_" + "5" * 32
    ledger = JobLedger(tmp_path / "jobs" / root, _req(max_cost=0))
    with pytest.raises(BudgetStop):
        ledger.reserve(0.001, purpose="child:T1", ref_id="T1")


def test_executor_fanout_reservation_rejects_without_running(tmp_path):
    """执行器：预留失败（零预算）的子任务不运行，显式记失败不静默跳过。"""
    import json
    from src.harness.model_gateway import BudgetStop, JobLedger
    root = "job_" + "6" * 32
    calls = {"n": 0}

    def script(req):
        calls["n"] += 1
        return (root, "不该被执行", "accepted")

    ex, requests, runs = _executor_with(script, workspace_root=tmp_path)
    plan = from_plan_dict(_fanout_plan_dict())
    record = ex.execute_plan(_req(max_cost=0, texts=()), plan, root_job_id=root)
    assert calls["n"] == 0                          # 零预算零请求
    assert record["degraded"] is True
    assert all("预算预留失败" in (s["failure"] or "") for s in record["subtasks"])


def test_role_config_effective_with_permission_intersection(tmp_path):
    """D2-03：角色工具声明生效；权限取"角色声明 ∩ 父级授权"，只能缩小不能放大。"""
    root = "job_" + "7" * 32
    ex, requests, runs = _executor_with(lambda r: (
        (root, "最终报告", "accepted") if "子题一" in r.task else
        ("job_s2", "x", "accepted") if "子题二" in r.task else
        (root, "最终报告", "accepted")), workspace_root=tmp_path)
    plan = from_plan_dict(_fanout_plan_dict())
    # 父级未限制：研究员的工具声明（web_search/fetch_page）整体生效
    record = ex.execute_plan(_req(), plan, root_job_id=root)
    eff = record["subtasks"][0]["effective"]
    assert eff["role_declared"] is True
    assert eff["role_declared_tools"] == ["web_search", "fetch_page"]
    assert eff["tools"] == ["web_search", "fetch_page"]
    # 父级授权收窄为 calculator：研究员实际工具被交集为空（权限只缩不放）
    record2 = ex.execute_plan(_req(allowed_tools=("calculator",)), plan, root_job_id=root)
    eff2 = record2["subtasks"][0]["effective"]
    assert eff2["tools"] == []
    sent = next(r for r in reversed(requests) if "子题一" in r.task)  # 第二轮的子请求
    assert sent.allowed_tools == ()                  # 交集结果落到子请求上
    assert sent.profile is None                      # 角色未声明非默认模型则不覆盖


def test_editor_role_exists_and_allowed_tools_validation():
    """D2-03：契约角色白名单与角色档案对齐；allowed_tools 字段校验。"""
    from src.agents.profiles import get_profile
    assert get_profile("editor").name == "editor"
    with pytest.raises(ValueError, match="allowed_tools"):
        TaskRequest(task="t", allowed_tools=("ok", 3))
    with pytest.raises(ValueError, match="allowed_tools"):
        TaskRequest(task="t", allowed_tools=(" ",))
    req = TaskRequest(task="t", allowed_tools=("calculator",))
    assert req.allowed_tools == ("calculator",)
    assert TaskRequest(task="t").allowed_tools is None   # 缺省=不限
