# -*- coding: utf-8 -*-
"""测试：Agent Loop 可以结束 / 最大循环阻止死循环（DEV_PLAN 步骤 13/14）。"""
import json

from src.graph.agent_loop import build_agent_graph
from src.llm.base import ChatResult, LLMAdapter, ToolCall
from src.llm.mock import MockLLM


class ConvergingStub(LLMAdapter):
    """第一次请求工具；看到 tool 结果后给出最终答案。"""

    model_name = "stub-converge"

    def chat(self, messages, tools=None):
        if any(m.get("role") == "tool" for m in messages):
            return ChatResult(content="答案是 1161")
        return ChatResult(tool_calls=[ToolCall(id="t1", name="calculator",
                                               arguments={"expression": "27*43"})])


def _invoke(question, llm, max_iterations):
    app = build_agent_graph(llm, max_iterations=max_iterations)
    return app.invoke({"messages": [{"role": "user", "content": question}],
                       "iteration": 0, "max_iterations": max_iterations})


def test_loop_converges_after_tool_result():
    result = _invoke("计算 27*43 然后回答", ConvergingStub(), max_iterations=5)
    assert result["iteration"] == 2          # 1 轮请求工具 + 1 轮收尾
    assert result["messages"][-1]["content"] == "答案是 1161"
    # 中间确实发生过真实 Tool Call（而不是模型心算）
    assert any(m.get("role") == "tool" for m in result["messages"])


def test_mock_loop_converges_after_tool_result():
    # MockLLM 现在"看到工具结果就收尾"：单工具任务应 2 轮收敛且结果真实
    result = _invoke("帮我计算 27*43", MockLLM(), max_iterations=5)
    assert result["iteration"] == 2
    assert "1161" in result["messages"][-1]["content"]
    assert result["messages"][-1]["content"].startswith("工具已返回内容")


class AlwaysToolStub(LLMAdapter):
    """永远请求工具的大脑：专门考验 Loop Guard。"""

    model_name = "stub-always-tool"

    def chat(self, messages, tools=None):
        return ChatResult(tool_calls=[
            ToolCall(id="t-always", name="calculator", arguments={"expression": "1+1"})])


def test_neverending_loop_stopped_by_limit():
    result = _invoke("帮我算一下", AlwaysToolStub(), max_iterations=3)
    assert result["iteration"] == 3          # 到顶被掐断
    last = result["messages"][-1]
    assert last["role"] == "assistant"
    assert "已达最大迭代限制" in last["content"]
    assert not last.get("tool_calls")        # 不再继续发工具请求


def test_assistant_payload_is_valid_openai_shape():
    from src.llm.base import ChatResult, ToolCall
    from src.graph.agent_loop import _assistant_payload

    payload = _assistant_payload(ChatResult(
        tool_calls=[ToolCall(id="x1", name="calculator", arguments={"expression": "1+1"})]))
    assert payload["role"] == "assistant"
    fn = payload["tool_calls"][0]["function"]
    assert fn["name"] == "calculator"
    assert json.loads(fn["arguments"]) == {"expression": "1+1"}
