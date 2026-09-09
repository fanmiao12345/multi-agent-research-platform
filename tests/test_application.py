import json
from dataclasses import replace

import pytest

from src.application.request import TaskRequest
from src.application.research import ResearchApplication
from src.harness.model_gateway import JobLedger, BudgetStop, job_scope, model_call
from src.llm.base import ChatResult
from src.llm.mock import MockLLM


class RealStub:
    model_name = "deepseek-chat"
    run_mode = "real"
    provider = "test"

    def __init__(self, usage=None):
        self.calls = 0
        self.usage = usage if usage is not None else {"prompt_tokens": 10, "completion_tokens": 2}

    def chat(self, messages, tools=None):
        self.calls += 1
        return ChatResult(content="done", usage=self.usage)


@pytest.mark.parametrize("fields", [
    {"task": []}, {"task": " "}, {"mode": "auto"}, {"max_calls": True},
    {"max_output_tokens": -1}, {"max_seconds": float("nan")}, {"max_cost": -1},
])
def test_request_rejects_invalid_limits(fields):
    with pytest.raises(ValueError):
        TaskRequest(**({"task": "test"} | fields))


def test_request_explicit_modes_and_zero_budget():
    request = TaskRequest.from_payload({"task": "test", "force_mock": True, "max_cost": 0})
    assert request.mode == "mock" and request.max_cost == 0
    with pytest.raises(ValueError):
        TaskRequest.from_payload({"task": "test", "force_mock": True, "mode": "real"})


@pytest.mark.parametrize("limits", [{"max_calls": 0}, {"max_cost": 0},
    {"max_output_tokens": 0}, {"max_seconds": 0}])
def test_application_zero_budget_never_calls_model(tmp_path, limits):
    llm = RealStub()
    result = ResearchApplication(TaskRequest("test", mode="real", **limits),
                                 llm=llm, workspace_root=tmp_path).run()
    assert llm.calls == 0 and result.termination_reason == "budget_exceeded"
    ledger = json.loads((tmp_path / "jobs" / result.root_job_id / "ledger.json").read_text())
    assert ledger["call_count"] == 0 and ledger["status"] == "cancelled"


def test_application_records_actual_run_and_no_prompt_in_ledger(tmp_path):
    result = ResearchApplication(TaskRequest("计算6*7"), llm=MockLLM(), workspace_root=tmp_path).run()
    data = json.loads((tmp_path / "jobs" / result.root_job_id / "ledger.json").read_text())
    assert "42" in result.final_text
    assert data["call_count"] == 2 and data["run_ids"] == [result.run_id]
    assert all(c["run_id"] == result.run_id and c["role"] == "agent" for c in data["calls"])
    assert data["estimated_cost_usd"] == 0 and "计算" not in json.dumps(data, ensure_ascii=False)
    meta = json.loads((tmp_path / result.run_id / "run.json").read_text())
    assert meta["root_job_id"] == result.root_job_id


def test_unknown_usage_stops_next_auxiliary_call(tmp_path):
    ledger = JobLedger(tmp_path / "job", TaskRequest("test", mode="real"))
    llm = RealStub(usage={})
    with job_scope(ledger):
        model_call(llm, [])
        with pytest.raises(BudgetStop, match="usage_unknown"):
            model_call(llm, [], purpose="judge")
    assert llm.calls == 1 and ledger.summary()["estimated_cost_usd"] is None


def test_auxiliary_calls_share_root_and_budget_stop_is_not_swallowed(tmp_path):
    from src.harness.planning.planner import plan_task
    from src.harness.planning.replanner import replan
    from src.harness.context.compressors import summarize_head
    ledger = JobLedger(tmp_path / "job", TaskRequest("test", mode="real", max_calls=2))
    llm = RealStub()
    with job_scope(ledger):
        plan = plan_task(llm, "test")
        replan(llm, plan, [])
        with pytest.raises(BudgetStop, match="call_limit"):
            summarize_head([], [{"content":"test"}], llm=llm)
    assert llm.calls == 2
    assert [c["purpose"] for c in ledger.calls] == ["planner", "replanner"]


def test_unknown_price_and_failure_are_not_free_calls(tmp_path):
    llm = RealStub()
    llm.model_name = "no-price"
    ledger = JobLedger(tmp_path / "unknown", TaskRequest("test", mode="real"))
    with job_scope(ledger), pytest.raises(BudgetStop, match="price_unknown"):
        model_call(llm, [])
    assert llm.calls == 0
    llm.model_name = "deepseek-chat"
    def fail(*args, **kwargs):
        raise RuntimeError("YOUR_TOKEN_HERE")
    llm.chat = fail
    ledger = JobLedger(tmp_path / "failure", TaskRequest("test", mode="real"))
    with job_scope(ledger), pytest.raises(RuntimeError):
        model_call(llm, [])
    saved = (ledger.directory / "ledger.json").read_text()
    assert "YOUR_TOKEN_HERE" not in saved
    assert ledger.summary()["unknown_usage_calls"] == 1


def test_output_limit_and_elapsed_time_stop(tmp_path):
    ledger = JobLedger(tmp_path / "tokens", TaskRequest("test", mode="real", max_output_tokens=2))
    with job_scope(ledger):
        model_call(RealStub(), [])
        with pytest.raises(BudgetStop, match="output_token_limit"):
            model_call(RealStub(), [])
    now = [0.0]
    ledger = JobLedger(tmp_path / "time", TaskRequest("test", max_seconds=1), clock=lambda: now[0])
    now[0] = 2
    with job_scope(ledger), pytest.raises(BudgetStop, match="time_limit"):
        model_call(MockLLM(), [])


def test_parallel_stages_inherit_one_root_and_serialize_budget(tmp_path):
    from src.orchestration.fanout import run_fanout
    ledger = JobLedger(tmp_path / "parallel", TaskRequest("test", mode="real", max_calls=1))
    llm = RealStub()
    def worker(task, role):
        return model_call(llm, [], role=role).content
    with job_scope(ledger), pytest.raises(BudgetStop, match="call_limit"):
        run_fanout("test", worker, ["a", "b"])
    assert llm.calls == 1 and len(ledger.calls) == 1


def test_write_intent_before_call_and_closed_job_cannot_spend(tmp_path, monkeypatch):
    ledger = JobLedger(tmp_path / "job", TaskRequest("test"))
    llm = RealStub()
    def broken_write():
        raise OSError("disk full")
    with monkeypatch.context() as m:
        m.setattr(ledger, "write", broken_write)
        with job_scope(ledger), pytest.raises(OSError):
            model_call(llm, [])
    assert llm.calls == 0
    ledger.finish("failed")
    with job_scope(ledger), pytest.raises(BudgetStop):
        model_call(llm, [])


def test_application_rejects_mode_mismatch(tmp_path):
    with pytest.raises(ValueError, match="模式不一致"):
        ResearchApplication(TaskRequest("test", mode="real"), llm=MockLLM(), workspace_root=tmp_path)


def test_judge_summary_and_rerank_are_in_root_ledger(tmp_path):
    from src.orchestration.debate import run_debate
    from src.harness.context.compressors import summarize_head
    from src.harness.skills.router import route
    from src.harness.skills.registry import SkillRegistry
    ledger = JobLedger(tmp_path / "job", TaskRequest("test", mode="real"))
    llm = RealStub()
    with job_scope(ledger):
        run_debate("test", lambda task, role: model_call(llm, [], role=role).content, llm)
        summarize_head([], [{"content": "test"}], llm)
        routed = route(SkillRegistry(), llm, "调研资料研究写作报告")
    assert routed["candidates"]
    assert {c["purpose"] for c in ledger.calls} >= {"judge", "summary", "rerank"}
    assert {c["role"] for c in ledger.calls} >= {"pro", "con", "judge"}
    assert ledger.summary()["call_count"] == llm.calls == 5


def test_parent_child_runs_inherit_job_through_tool_thread(tmp_path):
    from config.settings import Settings
    from src.harness.runtime.agent_runtime import AgentRuntime
    from src.harness.tools.executor import ToolExecutor
    from src.harness.tools.registry import ToolRegistry, ToolSpec
    from src.harness.model_gateway import RUN_ID
    ledger = JobLedger(tmp_path / "job", TaskRequest("test"))
    runtime = AgentRuntime(MockLLM(), settings=Settings(workspace_dir=tmp_path))
    registry = ToolRegistry()
    registry.register(ToolSpec("child", "test", lambda: runtime.run_task("你好").run_id, timeout=2))
    with job_scope(ledger):
        token = RUN_ID.set("parent-test-run")
        try:
            child = ToolExecutor(registry).execute("child", {})
        finally:
            RUN_ID.reset(token)
    assert len(ledger.run_ids) == 1
    meta = json.loads((tmp_path / ledger.run_ids[0] / "run.json").read_text())
    assert meta["parent_run_id"] == "parent-test-run" and meta["root_job_id"] == ledger.job_id
    assert ledger.calls[0]["run_id"] == ledger.run_ids[0]


def test_real_provider_receives_remaining_limits_and_no_hidden_retries(tmp_path, monkeypatch):
    from config.settings import Settings
    from types import SimpleNamespace
    from src.llm.provider import OpenAICompatibleLLM
    seen = {}
    def client(**kwargs):
        seen["client"] = kwargs
        def create(**options):
            seen["request"] = options
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok",tool_calls=[]))],
                usage=SimpleNamespace(prompt_tokens=1,completion_tokens=1))
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr("openai.OpenAI", client)
    settings = Settings(model_provider="deepseek",model_name="deepseek-chat",model_api_key="YOUR_API_KEY_HERE",
                        model_base_url="https://api.example",max_tokens=2048)
    llm = OpenAICompatibleLLM(settings)
    ResearchApplication(TaskRequest("test",mode="real",max_output_tokens=7,max_seconds=3),
                        llm=llm,settings=settings,workspace_root=tmp_path).run()
    assert seen["client"]["max_retries"] == 0
    assert seen["request"]["max_tokens"] == 7 and 0 < seen["request"]["timeout"] <= 3


def test_application_failure_persists_root_and_cli_uses_same_entry(tmp_path):
    import os
    import subprocess
    import sys
    llm = RealStub()
    def fail(*args, **kwargs):
        raise RuntimeError("YOUR_TOKEN_HERE")
    llm.chat = fail
    with pytest.raises(RuntimeError):
        ResearchApplication(TaskRequest("test",mode="real"),llm=llm,workspace_root=tmp_path).run()
    job = next((tmp_path / "jobs").glob("*/job.json"))
    assert json.loads(job.read_text())["status"] == "failed"
    assert "YOUR_TOKEN_HERE" not in job.read_text()
    cli_root = tmp_path / "cli"
    result = subprocess.run([sys.executable,"-m","src.interfaces.cli","计算6*7","--workspace",str(cli_root)],
                            capture_output=True,text=True,encoding="utf-8",env=dict(os.environ,PYTHONIOENCODING="utf-8"),timeout=15)
    assert result.returncode == 0, result.stderr
    result = json.loads(result.stdout)
    assert "42" in result["final_text"]
    assert (cli_root / "jobs" / result["root_job_id"] / "ledger.json").exists()
