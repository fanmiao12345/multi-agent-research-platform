# -*- coding: utf-8 -*-
"""
graph/hello_graph.py —— 第一个 LangGraph：Hello Graph（DEV_PLAN A0.4）

先不接 LLM，只验证最小闭环：StateGraph + State + Node + Edge + compile + invoke。

    START
      ↓
   hello_node
      ↓
     END

运行：python -m src.graph.hello_graph
学习点：StateGraph / State(TypedDict) / Node / Edge / compile / invoke
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class HelloState(TypedDict):
    """Graph State：只放当前 Thread 里参与流程控制的数据（文档 3.1 的雏形）。"""
    input: str
    output: str


def hello_node(state: HelloState) -> dict:
    """一个普通 Python 函数即 Node：输入当前 state，返回要合并的增量。"""
    return {"output": f"hello, {state['input']}"}


def build_graph():
    graph = StateGraph(HelloState)
    graph.add_node("hello_node", hello_node)
    graph.add_edge(START, "hello_node")
    graph.add_edge("hello_node", END)
    return graph.compile()  # compile 后才是可 invoke 的 Runnable


def main() -> None:
    app = build_graph()
    result = app.invoke({"input": "langgraph"})
    print(f"result = {result}")
    assert result["output"] == "hello, langgraph", "确定性输出"


if __name__ == "__main__":
    main()
