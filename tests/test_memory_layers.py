# -*- coding: utf-8 -*-
"""三层记忆：向量索引 / 遗忘曲线 / 分层门面 / Provider 与 User Message 注入。"""
import math

import pytest

from src.harness.context.builder import compose_context
from src.harness.context.policy import ContextSource
from src.harness.memory.layers import (MemoryLayers, WorkingMemory,
                                       retrievability)
from src.harness.memory.long_term import LongTermStore
from src.harness.memory.provider import (LayeredMemoryProvider,
                                         build_user_message,
                                         inject_into_user,
                                         injection_report)
from src.harness.memory.vector_store import (HashingVectorIndex, build_index,
                                             cosine, embed)


# ---- 向量存储 ----

def test_embed_deterministic_and_normalized():
    a1 = embed("并发上限是 3 个子智能体")
    a2 = embed("并发上限是 3 个子智能体")
    b = embed("报告修订轮数为 2")
    assert a1 == a2                                   # 同文本同向量
    assert abs(math.sqrt(sum(x * x for x in a1)) - 1.0) < 1e-6  # 已归一化
    assert cosine(a1, b) < 0.999                      # 不同文本相似度更低


def test_hashing_index_add_search_remove(tmp_path):
    index = HashingVectorIndex()
    index.add("m1", "默认并发上限 3 个子智能体")
    index.add("m2", "报告修订轮数上限 2 轮")
    hits = index.search("最多并行几个子智能体", top_k=1)
    assert hits[0]["id"] == "m1"
    index.remove("m1")
    hits = index.search("报告修订轮数上限", top_k=2)
    assert all(h["id"] != "m1" for h in hits)
    assert hits and hits[0]["id"] == "m2"

    path = index.save(tmp_path / "vec.json")
    loaded = HashingVectorIndex.load(path)
    assert loaded.count() == 1
    assert loaded.search("报告修订轮数上限", top_k=1)[0]["id"] == "m2"


def test_build_index_auto_returns_working_backend():
    index = build_index(backend="auto")     # sqlite_vec 未安装时自动回退
    assert index.backend in ("sqlite_vss", "hashing_cosine")
    index.add("x", "事实内容")
    assert index.count() == 1


def test_vss_backend_matches_hashing_reference_when_available():
    """装了 sqlite-vec 时，vss 后端与零依赖参考实现必须给出一致结果
    （L2→余弦换算正确性）；未安装则跳过。"""
    pytest.importorskip("sqlite_vec")
    from src.harness.memory.vector_store import SqliteVssIndex

    texts = ["项目的默认并发上限是 3 个子智能体", "报告修订轮数上限 2 轮"]
    vss = SqliteVssIndex()
    ref = HashingVectorIndex()
    for i, text in enumerate(texts):
        vss.add(f"m{i}", text)
        ref.add(f"m{i}", text)
    vss_hit = vss.search("最多并行几个子智能体", top_k=1)[0]
    ref_hit = ref.search("最多并行几个子智能体", top_k=1)[0]
    assert vss_hit["id"] == ref_hit["id"] == "m0"
    assert abs(vss_hit["score"] - ref_hit["score"]) < 1e-3


# ---- 遗忘曲线 ----

def test_retrievability_decays_with_days_and_stability():
    from src.harness.memory.long_term import MemoryRecord
    rec = MemoryRecord(id="a", kind="episodic", content="x",
                       created_at="2020-01-01T00:00:00", stability=1.0)
    assert retrievability(rec) < 0.01                 # 久未访问 → 接近遗忘
    rec.stability = 5000.0                            # 高稳定 → 慢衰减
    assert retrievability(rec) > 0.5
    fresh = MemoryRecord(id="b", kind="semantic", content="y")
    assert retrievability(fresh) == 1.0               # 刚创建 = 完整可检索


def test_working_memory_bounded_fifo():
    working = WorkingMemory(max_items=3)
    for i in range(5):
        working.add(f"条目{i}")
    dumped = working.dump()
    assert [d["content"] for d in dumped] == ["条目2", "条目3", "条目4"]
    assert working.clear() == 3 and working.dump() == []


# ---- 分层门面：跨会话复用与衰减 ----

@pytest.fixture
def layers(tmp_path):
    return MemoryLayers(store=LongTermStore(tmp_path / "mem.json"))


def test_semantic_remember_and_cross_query_retrieval(layers):
    layers.remember_semantic("单来源正文导入上限为 2MB", source="会话A")
    hits = layers.semantic.search("一个文件最大能导入多大？", top_k=1)
    assert hits and "2MB" in hits[0].content
    assert hits[0].stability > 1.0                    # 被召回 → 强化


def test_layers_snapshot_and_search_all(layers):
    layers.remember_semantic("预算耗尽时交付草稿并说明缺口")
    layers.remember_episodic("上次跑批遇到超时，重试后成功")
    layers.remember_working("当前正在整理第 3 节")
    grouped = layers.search_all("预算耗尽怎么办")
    assert grouped["semantic"] and grouped["episodic"] and grouped["working"]
    snap = layers.snapshot()
    assert snap["semantic"] == 1 and snap["episodic"] == 1
    assert snap["working"] == 1


def test_decay_forgets_stale_episodic_keeps_semantic(layers):
    rec_ep = layers.remember_episodic("三个月前的一次失败经验")
    rec_se = layers.remember_semantic("核心事实：引用必须可定位")
    # 人为把两条都调到很久以前 + 低稳定度
    for rec in (rec_ep, rec_se):
        rec.stability = 0.1
        rec.last_access = "2020-01-01T00:00:00"
    layers.store._flush()
    result = layers.decay(threshold=0.05)
    assert rec_ep.id in result["forgotten_episodic"]
    assert layers.store.get(rec_ep.id) is None        # 经验被遗忘（物理删除）
    assert layers.store.get(rec_se.id) is not None    # 事实保留（只降权不删）


# ---- Provider 抽象与 Prompt Cache 友好注入 ----

def test_layered_provider_renders_layers(layers):
    layers.remember_semantic("报告里的推断必须标注〔推断〕标签")
    provider = LayeredMemoryProvider(layers)
    text = provider.render("推断的内容要怎么标注？")
    assert "[语义/" in text and "推断" in text
    assert provider.stats()["name"] == "layered_memory"


def test_inject_into_user_keeps_system_prefix_stable():
    messages = [{"role": "system", "content": "系统提示词（前缀）"},
                {"role": "user", "content": "本轮问题"}]
    out = inject_into_user(messages, "相关记忆：默认并发 3", question="本轮问题")

    assert out[0]["content"] == messages[0]["content"]        # system 逐字节不变
    assert "<<CONTEXT>>" in out[1]["content"]                 # 标记保留（网关识别）
    assert "[本轮问题]" in out[1]["content"] and "并发" in out[1]["content"]
    report = injection_report(messages, out)
    assert report["system_prefix_stable"] is True
    assert report["added_tokens_est"] > 0

    again = inject_into_user(out, "再来一条记忆")              # 已注入不重复叠
    assert again[1]["content"] == out[1]["content"]

    assert inject_into_user(messages, "")[1]["content"] == "本轮问题"  # 空块原样


def test_build_user_message_format():
    text = build_user_message("问题Q", "记忆M")
    assert text.startswith("<<CONTEXT>>")
    assert "[记忆]\n记忆M" in text and "[本轮问题]\n问题Q" in text
    assert build_user_message("问题Q", "") == "问题Q"


# ---- compose_context：user_message_kinds ----

def _sources():
    return [ContextSource(kind="memory", content="记忆块"),
            ContextSource(kind="evidence", content="证据块"),
            ContextSource(kind="instructions", content="指令块")]


def test_compose_context_default_unchanged():
    messages, stats = compose_context("问题", _sources())
    kinds = [m["role"] for m in messages]
    assert "system" in kinds and kinds.count("system") == 2   # 指令 + <<CONTEXT>>
    assert any("<<CONTEXT>>" in str(m.get("content")) and m["role"] == "system"
               for m in messages)


def test_compose_context_user_message_kinds_goes_into_user():
    messages, stats = compose_context("问题", _sources(),
                                      user_message_kinds=("memory", "evidence"))
    system_msgs = [m for m in messages if m["role"] == "system"]
    assert len(system_msgs) == 1                       # 只有指令，无 <<CONTEXT>> 系统消息
    assert "<<CONTEXT>>" not in system_msgs[0]["content"]
    user_msg = messages[-1]
    assert user_msg["role"] == "user"
    assert "[本轮问题]" in user_msg["content"]
    assert "[memory]" in user_msg["content"] and "[evidence]" in user_msg["content"]
    assert "指令块" in system_msgs[0]["content"]        # 指令仍在 system
