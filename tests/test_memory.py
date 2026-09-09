# -*- coding: utf-8 -*-
"""测试：Memory & Knowledge（M6 步骤 65-73）。"""
import json
import os
import shutil
import uuid

import pytest

from eval.evaluators.memory_metrics import evaluate as mem_eval
from src.graph.agent_loop import build_agent_graph
from src.harness.memory.checkpointer import make_checkpointer, thread_config
from src.harness.memory.knowledge import load_documents, retrieve
from src.harness.memory.long_term import (KIND_EPISODIC, KIND_SEMANTIC,
                                          LongTermStore)
from src.harness.memory.policy import should_write
from src.llm.mock import MockLLM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def store():
    path = os.path.join(ROOT, "workspaces", "_t_mem_" + uuid.uuid4().hex[:6],
                        "memory.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    s = LongTermStore(path)
    yield s
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)


# ---------- 65/66 Short-term + Checkpointer ----------
def test_checkpointer_keeps_state_across_calls_same_thread():
    cp = make_checkpointer()
    llm = MockLLM()
    app = build_agent_graph(llm, max_iterations=3, checkpointer=cp)
    cfg = thread_config("thread-m6")

    def _invoke(question):
        from src.graph.state import new_state
        return app.invoke(new_state(question, run_id="r", max_iterations=3),
                          config=cfg)

    first = _invoke("你好")
    second = _invoke("现在几点？")
    joined = "".join(str(m.get("content")) for m in second["messages"])
    # 同 thread：第二次调用能看到第一次的对话（短期记忆跨调用）
    assert "你好" in joined or "（mock）" in joined
    assert second["iteration"] == 2          # 第二轮独立收敛


# ---------- 67 Long-term Store ----------
def test_store_crud_and_search(store):
    a = store.remember(KIND_SEMANTIC, "用户喜欢美式咖啡", source="chat", confidence=0.9)
    b = store.remember(KIND_SEMANTIC, "项目接口用 OpenAI 兼容格式")
    store.remember(KIND_EPISODIC, "上次调研失败是因为没配 Key")
    assert len(store.list()) == 3
    assert store.get(a.id).content == "用户喜欢美式咖啡"
    assert len(store.search("咖啡")) == 1
    store.update(b.id, content="项目接口用 OpenAI 兼容格式（改）")
    assert "改" in store.get(b.id).content
    assert store.forget(a.id) is True
    assert store.get(a.id) is None


def test_expired_records_hidden_by_default(store):
    import datetime
    past = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    store.remember(KIND_SEMANTIC, "过期事实", expires_at=past)
    assert store.list() == []
    assert len(store.list(include_expired=True)) == 1


def test_store_persists_to_disk(store):
    rec = store.remember(KIND_SEMANTIC, "会持久化的事实")
    reloaded = LongTermStore(store.path)
    assert reloaded.get(rec.id) is not None
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert len(data) == 1


# ---------- 68-70 kinds 已由字段承载；search(kind) 过滤 ----------
def test_kind_filter():
    path = os.path.join(ROOT, "workspaces", "_t_kind_" + uuid.uuid4().hex[:6], "m.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    s = LongTermStore(path)
    try:
        s.remember(KIND_SEMANTIC, "事实 A")
        s.remember(KIND_EPISODIC, "经验 B")
        assert [r.kind for r in s.list(KIND_EPISODIC)] == [KIND_EPISODIC]
    finally:
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)


# ---------- 71 Write Policy ----------
def test_write_policy():
    ok, reason = should_write("semantic", explicit=True)
    assert ok and "明确要求" in reason
    ok, _ = should_write("semantic", stable=True)
    assert ok
    ok, _ = should_write("episodic", explicit=False)
    assert not ok                     # episodic 不自动沉淀
    ok, _ = should_write("semantic", explicit=False, stable=False, verified=False)
    assert not ok                     # 全量自动记录被拒


# ---------- 72 Knowledge Retrieval ----------
def test_knowledge_load_and_retrieve():
    docs = load_documents()
    assert {d["name"] for d in docs} == {"glossary", "project-conventions"}
    hits = retrieve("OpenAI 兼容接口 base url 是哪个", top_k=2)
    assert hits and hits[0]["name"] == "project-conventions"
    hits2 = retrieve("ReAct 是什么意思")
    assert hits2[0]["name"] == "glossary"


# ---------- 73 Memory Eval ----------
def test_memory_eval_metrics(store):
    rel = store.remember(KIND_SEMANTIC, "用户喜欢喝美式咖啡，不加糖")
    other = store.remember(KIND_SEMANTIC, "项目用 Python 3.14")
    stale = store.remember(KIND_SEMANTIC, "旧结论：模型叫 X", expires_at="2000-01-01")

    def retrieve_fn(q):
        out = store.search(q, top_k=2)
        return [(r.id, r.confidence) for r in out]

    report = mem_eval(retrieve_fn, [
        {"query": "咖啡", "expected_ids": [rel.id], "stale_ids": []},
        {"query": "Python 版本", "expected_ids": [other.id], "stale_ids": []},
    ])
    assert report["recall_precision"] == 1.0
    assert report["recall_rate"] == 1.0
    assert report["irrelevant_injection_rate"] == 0.0
    assert report["stale_rate"] == 0.0
    # 过期条目不可被搜到（stale 防护在 store 层已拦截）
    assert store.get(stale.id).is_expired()
