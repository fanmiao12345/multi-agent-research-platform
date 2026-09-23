# -*- coding: utf-8 -*-
"""Q4-D1 修复之三：结构化短输出阶段关闭 thinking（2026-09-23 拍板）。

实测：deepseek-v4-flash 默认带思维链，reasoning token 计入 completion 但不在
content 里——证据抽取可见载荷 ~1k 字符却计费 3.8k token（14 来源 ~50k），
是研究链预算的第二放大器。选型/检索规划/证据/素材/提纲/审校/派工裁决等
边界清晰的结构化任务关闭 thinking；初稿写作与 agent 流保留（质量优先）。
"""
import json

import pytest

from config.settings import Settings
from src.application.request import TaskRequest
from src.harness.model_gateway import JobLedger, job_scope, model_call


class _RecordingLLM:
    """记录 chat_limited 收到的 kwargs；返回构造时给定的文本。"""

    run_mode = "mock"          # mock 口径：不触发未知价格/未知用量熔断（记账测试与模型无关）
    model_name = "fake"
    provider = "fake"

    def __init__(self, content):
        self.content = content
        self.seen_kwargs = []

    def chat_limited(self, messages, tools=None, **kwargs):
        self.seen_kwargs.append(kwargs)
        from src.llm.base import ChatResult
        return ChatResult(content=self.content, usage={"prompt_tokens": 1,
                                                       "completion_tokens": 1})

    def chat(self, messages, tools=None):
        return self.chat_limited(messages, tools)


def _ledger(tmp_path):
    return JobLedger(tmp_path / "jobs" / "job_test", TaskRequest(task="t"))


# ---- provider 层 -------------------------------------------------------------
def _make_llm(monkeypatch, fail_first_with=None):
    """桩掉 openai 客户端，记录每次 create 的 options 与调用次数。"""
    from types import SimpleNamespace
    seen = {"calls": []}

    def client(**kwargs):
        def create(**options):
            seen["calls"].append(options)
            if fail_first_with is not None and len(seen["calls"]) == 1:
                raise fail_first_with
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content="ok", tool_calls=[]))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr("openai.OpenAI", client)
    settings = Settings(model_provider="deepseek", model_name="deepseek-chat",
                        model_api_key="YOUR_API_KEY_HERE",
                        model_base_url="https://api.example", max_tokens=2048)
    from src.llm.provider import OpenAICompatibleLLM
    return OpenAICompatibleLLM(settings), seen


def test_provider_sends_thinking_disabled_only_when_requested(monkeypatch):
    llm, seen = _make_llm(monkeypatch)
    llm.chat_limited([{"role": "user", "content": "x"}], max_tokens=100,
                     disable_thinking=True)
    assert seen["calls"][-1].get("extra_body") == {"thinking": {"type": "disabled"}}
    llm.chat_limited([{"role": "user", "content": "x"}], max_tokens=100)
    assert "extra_body" not in seen["calls"][-1]


def test_provider_retries_without_thinking_param_if_rejected(monkeypatch):
    class _Rejects(ValueError):
        pass
    llm, seen = _make_llm(monkeypatch, fail_first_with=_Rejects("unknown field extra_body"))
    reply = llm.chat_limited([{"role": "user", "content": "x"}], max_tokens=100,
                             disable_thinking=True)
    assert reply.content == "ok"
    assert len(seen["calls"]) == 2                      # 第一次带参数被拒，第二次退回
    assert "extra_body" not in seen["calls"][-1]


# ---- 网关与调用点 ------------------------------------------------------------
def test_model_call_forwards_disable_thinking_through_ledger(tmp_path):
    llm = _RecordingLLM("ok")
    with job_scope(_ledger(tmp_path)):
        model_call(llm, [{"role": "user", "content": "x"}], purpose="t", role="r",
                   disable_thinking=True)
        assert llm.seen_kwargs[-1].get("disable_thinking") is True
        model_call(llm, [{"role": "user", "content": "x"}], purpose="t", role="r")
        assert "disable_thinking" not in llm.seen_kwargs[-1]


def test_ledger_tolerates_adapters_without_thinking_kwarg(tmp_path):
    class _OldAdapter(_RecordingLLM):
        def chat_limited(self, messages, tools=None, max_tokens=None, timeout=None):
            return super().chat_limited(messages, tools=tools, max_tokens=max_tokens,
                                        timeout=timeout)

    llm = _OldAdapter("ok")
    with job_scope(_ledger(tmp_path)):
        reply = model_call(llm, [{"role": "user", "content": "x"}], purpose="t",
                           role="r", disable_thinking=True)
    assert reply.content == "ok"                        # 降级不报错，只是多花 token
    assert "disable_thinking" not in llm.seen_kwargs[-1]


def _plan_json():
    return json.dumps({"schema_version": "1", "mode": "fixed", "reason": "单点成稿",
                       "subtasks": [{"id": "T1", "role": "writer", "description": "成稿",
                                     "depends_on": [], "parallel": False,
                                     "covers_sections": []}],
                       "budget": {"max_calls": 40, "max_cost_usd": 0.15,
                                  "max_seconds": 600},
                       "fallback_mode": "fixed"}, ensure_ascii=False)


def test_structured_stages_disable_thinking_but_draft_keeps_it(tmp_path):
    """选型/证据/素材/提纲/审校 关 thinking；draft（写作）保留。"""
    from src.application.orchestration.scheduler import OrchestrationScheduler
    from src.application.pipeline.draft import run_draft_stage
    from src.application.pipeline.evidence import extract_source_evidence
    from src.application.pipeline.material import run_material_stage
    from src.application.pipeline.outline import run_outline_stage
    from src.application.pipeline.review import model_review
    from src.application.pipeline.model import OutlineSection

    goal = "主题"
    source = {"source_id": "s1", "title": "t", "display": "资料一",
              "text": "本地运行是关键特性。", "segments": []}
    cases = [
        ("scheduler", lambda llm: OrchestrationScheduler(llm).plan(goal), _plan_json()),
        ("evidence", lambda llm: extract_source_evidence(
            llm, goal, dict(source)), json.dumps(
            {"items": [{"fact": "本地运行", "tag": "F", "quote": "本地运行"}]},
            ensure_ascii=False)),
        ("material", lambda llm: run_material_stage(
            llm, goal, [{"evidence_id": "E-001", "fact": "f", "tag": "F",
                         "source_id": "s1"}], labels={}), json.dumps(
            {"topics": [], "conflicts": [], "gaps": []}, ensure_ascii=False)),
        ("outline", lambda llm: run_outline_stage(
            llm, goal, "素材", {"E-001"}), json.dumps(
            {"title": "T", "sections": [{"heading": "一", "purpose": "p",
                                         "required_evidence": ["E-001"],
                                         "require_fact_markers": False}]},
            ensure_ascii=False)),
        ("review", lambda llm: model_review(
            llm, goal, "正文", "- E-001 f", [OutlineSection("一")]), json.dumps(
            {"issues": [], "verdict": "accepted"}, ensure_ascii=False)),
    ]
    for name, call, reply in cases:
        llm = _RecordingLLM(reply)
        with job_scope(_ledger(tmp_path)):
            call(llm)
        assert llm.seen_kwargs[-1].get("disable_thinking") is True, name

    llm = _RecordingLLM(json.dumps(
        {"report_markdown": "# 标题\n\n## 一\n\n" + "这是为了通过长度校验的正文内容。" * 20},
        ensure_ascii=False))
    with job_scope(_ledger(tmp_path)):
        run_draft_stage(llm, goal, [OutlineSection("一")], "标题",
                        {"topics": [], "conflicts": [], "gaps": []})
    assert "disable_thinking" not in llm.seen_kwargs[-1]   # 写作保留思考
