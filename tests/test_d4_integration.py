# -*- coding: utf-8 -*-
"""D4：Context Builder、Skill、Memory/Knowledge、Handoff 与 Thread 短期记忆接线。"""
import json

from config.settings import Settings
from src.harness.memory.long_term import LongTermStore
from src.harness.runtime.agent_runtime import AgentRuntime
from src.harness.runtime.run_context import RuntimeContext
from src.llm.base import ChatResult


class CaptureLLM:
    model_name = "mock-rule-v1"
    run_mode = "mock"
    provider = "stub"

    def __init__(self, content="完成"):
        self.content = content
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": messages, "tools": tools or []})
        return ChatResult(content=self.content, usage={})


def _joined(call):
    return "\n".join(str(m.get("content") or "") for m in call["messages"])


def test_context_builder_skill_memory_knowledge_and_handoff(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "接口.md").write_text(
        "OpenAI 兼容接口 base url 使用项目配置。", encoding="utf-8")
    memory = LongTermStore(tmp_path / "memory.json")
    memory.remember("semantic", "用户偏好中文输出", source="user:explicit")
    llm = CaptureLLM()
    runtime = AgentRuntime(
        llm, settings=Settings(workspace_dir=tmp_path), memory_store=memory,
        knowledge_root=knowledge)
    ctx = RuntimeContext.from_settings(
        runtime.settings, permissions=frozenset({"calculator"}),
        handoff_text="交接卡：只使用已授权资料。")
    outcome = runtime.run_task(
        "帮我计算 6*7，并用中文说明 OpenAI 兼容接口 base url",
        context=ctx, system_extra="不要泄露内部上下文。")

    call = llm.calls[0]
    text = _joined(call)
    assert "[skill]" in text and "quick-math" in text
    assert "[memory]" in text and "用户偏好中文输出" in text
    assert "[evidence]" in text and "OpenAI 兼容接口" in text
    assert "[handoff]" in text and "交接卡" in text
    assert call["tools"] and [t["function"]["name"] for t in call["tools"]] == ["calculator"]
    record = json.loads((tmp_path / outcome.run_id / "context.json")
                        .read_text(encoding="utf-8"))
    assert record["skill"]["skill"] == "quick-math"
    assert record["records"][0]["stats"]["total_estimated"] > 0


def test_skill_cannot_elevate_permissions(tmp_path):
    llm = CaptureLLM()
    runtime = AgentRuntime(llm, settings=Settings(workspace_dir=tmp_path))
    ctx = RuntimeContext.from_settings(
        runtime.settings, permissions=frozenset({"current_time"}))
    runtime.run_task("帮我计算 6*7", context=ctx)
    assert llm.calls[0]["tools"] == []


def test_explicit_memory_can_be_viewed_and_deleted(tmp_path):
    memory = LongTermStore(tmp_path / "memory.json")
    runtime = AgentRuntime(
        CaptureLLM(), settings=Settings(workspace_dir=tmp_path), memory_store=memory)
    runtime.run_task("请记住：我偏好中文输出")
    records = memory.list()
    assert len(records) == 1 and "中文输出" in records[0].content
    assert records[0].source.startswith("explicit:")
    assert memory.forget(records[0].id) is True
    assert memory.list() == []


def test_thread_checkpointer_keeps_short_term_history(tmp_path):
    llm = CaptureLLM()
    runtime = AgentRuntime(llm, settings=Settings(workspace_dir=tmp_path))
    ctx = RuntimeContext.from_settings(runtime.settings, thread_id="thread-d4")
    runtime.run_task("第一轮：记住当前讨论主题是星桥项目", context=ctx)
    runtime.run_task("第二轮：继续说明", context=ctx)
    second = _joined(llm.calls[-1])
    assert "第一轮" in second and "第二轮" in second


def test_disabled_memory_does_not_write_or_retrieve(tmp_path):
    memory = LongTermStore(tmp_path / "memory.json")
    memory.remember("semantic", "LEGACY_MEMORY_ONLY")
    llm = CaptureLLM()
    runtime = AgentRuntime(
        llm, settings=Settings(workspace_dir=tmp_path), memory_store=memory)
    ctx = RuntimeContext.from_settings(runtime.settings, memory_enabled=False)
    runtime.run_task("请记住：我偏好中文输出；继续用旧偏好", context=ctx)
    assert len(memory.list()) == 1
    assert "LEGACY_MEMORY_ONLY" not in _joined(llm.calls[0])