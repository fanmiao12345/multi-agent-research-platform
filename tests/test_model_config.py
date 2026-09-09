"""模型模式边界与配置诊断：真实适配器使用替身传输，全程无网络请求。"""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from config.settings import Settings
from src.harness.models.factory import ModelConfigError, build_adapter, diagnose_config
from src.interfaces.web.workbench import WorkbenchState


@pytest.fixture
def settings():
    return Settings(model_provider="deepseek", model_name="deepseek-chat",
                    model_api_key="YOUR_API_KEY_HERE", model_base_url="https://api.example/v1",
                    temperature=0.4, max_tokens=123)


@pytest.fixture
def sdk(monkeypatch):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        msg = SimpleNamespace(content="4", tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=1))
    def client(**kwargs):
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr("openai.OpenAI", client)
    return calls


def test_real_preserves_config_and_explicit_profile(settings, sdk):
    configured = replace(settings, model_name="configured-model")
    llm = build_adapter(settings=configured, mode="real")
    assert llm.model_name == "configured-model"
    llm.chat([{"role": "user", "content": "2+2"}])
    assert sdk[0]["model"] == "configured-model"
    assert sdk[0]["temperature"] == 0.4 and sdk[0]["max_tokens"] == 123
    assert build_adapter("deep", settings, mode="real").model_name == "deepseek-reasoner"
    assert diagnose_config(configured, mode="real")["model"] == "configured-model"


@pytest.mark.parametrize("changes,phrase", [
    ({"model_api_key": ""}, "MODEL_API_KEY"),
    ({"model_provider": "mock"}, "MODEL_PROVIDER"),
    ({"model_provider": "anthropic"}, "MODEL_PROVIDER"),
    ({"model_base_url": "https://user:YOUR_PASSWORD_HERE@api.example"}, "MODEL_BASE_URL"),
    ({"model_base_url": "https://api.example?key=YOUR_API_KEY_HERE"}, "MODEL_BASE_URL"),
    ({"model_base_url": "https://api.example:bad"}, "MODEL_BASE_URL"),
    ({"model_name": ""}, "MODEL_NAME"),
    ({"temperature": float("nan")}, "TEMPERATURE"),
    ({"max_tokens": 0}, "MAX_TOKENS"),
])
def test_invalid_real_config_never_falls_back(settings, changes, phrase):
    settings = replace(settings, **changes)
    with pytest.raises(ModelConfigError, match=phrase):
        build_adapter(settings=settings, mode="real")
    report = diagnose_config(settings, mode="real")
    assert not report["ready"] and not report["connection_tested"]
    assert "YOUR_API_KEY_HERE" not in json.dumps(report)
    assert "YOUR_PASSWORD_HERE" not in json.dumps(report)


def test_conflicting_flags_and_profile_provider(settings):
    with pytest.raises(ModelConfigError, match="冲突"):
        build_adapter(settings=settings, mode="real", force_mock=True)
    with pytest.raises(ModelConfigError, match="不匹配"):
        build_adapter("balanced", replace(settings, model_provider="openai"), mode="real")
    with pytest.raises(ModelConfigError, match="布尔"):
        build_adapter(settings=settings, force_mock="false")
    with pytest.raises(ModelConfigError, match="档案"):
        build_adapter("nonexistent", settings, mode="mock")


def test_diagnostics_do_not_create_client_or_expose_key(settings, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("静态诊断不能实例化SDK")
    monkeypatch.setattr("openai.OpenAI", forbidden)
    result = diagnose_config(settings, mode="real")
    assert result["ready"] and not result["connection_tested"]
    assert "YOUR_API_KEY_HERE" not in json.dumps(result) + repr(settings)
    assert "api.example" not in json.dumps(result) + repr(settings)
    assert diagnose_config(replace(settings, model_api_key=""), mode="mock")["ready"]


def test_invalid_environment_value_is_not_echoed(monkeypatch):
    monkeypatch.setenv("MAX_TOKENS", "YOUR_TOKEN_HERE")
    result = diagnose_config(mode="real")
    assert not result["ready"] and "YOUR_TOKEN_HERE" not in json.dumps(result)


def test_real_web_run_records_actual_adapter(settings, sdk, tmp_path):
    state = WorkbenchState(tmp_path, settings=settings)
    result = state.start_run({"task": "不要调用工具，2+2是多少", "mode": "real"})
    with state.lock:
        worker = state.running.get(result["run_id"])
    if worker:
        worker.join(10)
        assert not worker.is_alive()
    meta = json.loads((tmp_path / result["run_id"] / "run.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "real" and meta["provider"] == "deepseek"
    assert meta["model"] == settings.model_name and meta["final_text"] == "4"
    assert len(sdk) == 1


def test_real_call_failure_has_no_provider_body(settings, sdk, monkeypatch):
    llm = build_adapter(settings=settings, mode="real")
    def fail(**kwargs):
        raise RuntimeError("Authorization YOUR_TOKEN_HERE")
    monkeypatch.setattr(llm._client.chat.completions, "create", fail)
    with pytest.raises(RuntimeError) as caught:
        llm.chat([])
    assert "YOUR_TOKEN_HERE" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_real_benchmark_uses_provider_and_records_mode(settings, sdk, tmp_path):
    from eval.benchmark import run_benchmark
    report = run_benchmark(tmp_path, run_only_safe=False, task_filter="b03",
                           mode="real", settings=settings, max_cost=0.01)
    assert len(sdk) == 1
    assert report["meta"]["mode"] == "real"
    assert report["meta"]["brain"] == "deepseek-chat"
    assert report["totals"]["passed"] == 1


def test_benchmark_blocks_implicit_real_and_missing_budget(settings, sdk, tmp_path):
    from eval.benchmark import run_benchmark
    with pytest.raises(ValueError, match="--mode real"):
        run_benchmark(tmp_path, run_only_safe=False)
    with pytest.raises(ValueError, match="max-cost"):
        run_benchmark(tmp_path, mode="real", settings=settings)
    report = run_benchmark(tmp_path, mode="real", settings=settings, max_cost=0)
    assert sdk == [] and report["totals"]["executed"] == 0
    assert report["totals"]["success_rate"] is None


def test_benchmark_skip_reasons_and_failure_report(settings, sdk, tmp_path, monkeypatch):
    from eval.benchmark import render_markdown, run_benchmark
    report = run_benchmark(tmp_path, mode="real", settings=settings, max_cost=0.01,
                           task_filter="b05")
    assert report["results"][0]["reason"] == "mock_only_expectation"
    report = run_benchmark(tmp_path, mode="real", settings=settings, max_cost=0.01,
                           task_filter="b08", run_only_safe=False)
    assert report["results"][0]["reason"] == "capability_missing" and not sdk
    def fail(*args, **kwargs):
        raise RuntimeError("YOUR_TOKEN_HERE")
    monkeypatch.setattr("src.llm.provider.OpenAICompatibleLLM.chat_limited", fail)
    report = run_benchmark(tmp_path, mode="real", settings=settings, max_cost=0.01)
    assert report["totals"]["failed"] == 1
    assert report["meta"]["estimated_cost_usd"] is None
    assert "YOUR_TOKEN_HERE" not in json.dumps(report) + render_markdown(report, None)
    assert any(r.get("reason") == "budget_unknown" for r in report["results"])


def test_benchmark_stops_remaining_tasks_after_spend(settings, sdk, tmp_path):
    from eval.benchmark import run_benchmark
    report = run_benchmark(tmp_path, mode="real", settings=settings, max_cost=0.000001,
                           run_only_safe=False)
    assert len(sdk) == 1
    assert report["totals"]["failed"] == 1
    assert any(r.get("reason") == "budget_exhausted" for r in report["results"])


def test_provider_tool_call_roundtrip(settings, sdk, tmp_path, monkeypatch):
    from src.harness.runtime.agent_runtime import AgentRuntime
    from src.harness.runtime.run_context import RuntimeContext
    llm = build_adapter(settings=settings, mode="real")
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        tool_calls = [SimpleNamespace(id="test-call", function=SimpleNamespace(
            name="calculator", arguments='{"expression":"6*7"}'))] if len(requests) == 1 else []
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=None if tool_calls else "42", tool_calls=tool_calls))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2))
    monkeypatch.setattr(llm._client.chat.completions, "create", create)
    result = AgentRuntime(llm, settings=settings).run_task("计算6*7",
        RuntimeContext.from_settings(settings, workspace_path=tmp_path, max_cost=0.01))
    assert result.final_text == "42" and len(requests) == 2
    assert requests[0]["tool_choice"] == "auto"
    assert any(m.get("role") == "tool" and "42" in m["content"] for m in requests[1]["messages"])
