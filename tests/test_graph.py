# -*- coding: utf-8 -*-
"""测试：Graph 可编译 / hello 确定性（DEV_PLAN 步骤 14）。"""
from src.graph.agent_loop import build_agent_graph
from src.graph.hello_graph import build_graph


def test_hello_graph_compiles_and_runs():
    app = build_graph()
    result = app.invoke({"input": "pytest"})
    assert result["output"] == "hello, pytest"


def test_agent_loop_graph_compiles():
    from src.llm.mock import MockLLM

    app = build_agent_graph(MockLLM(), max_iterations=3)
    # 纯文本问题：一轮即结束，不需要任何工具
    result = app.invoke({
        "messages": [{"role": "user", "content": "你好"}],
        "iteration": 0, "max_iterations": 3,
    })
    assert result["iteration"] == 1
    assert "（mock）" in result["messages"][-1]["content"]
