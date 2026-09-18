# -*- coding: utf-8 -*-
"""
harness/memory/layers.py —— 三层记忆管理（Working / Episodic / Semantic）

| 层 | 生命周期 | 存储 | 用途 |
|---|---|---|---|
| Working  | 会话内     | 内存（有界）   | 当前任务的过程材料，会话结束即弃 |
| Episodic | 跨会话     | LongTermStore  | 历史任务/成败经验；受遗忘曲线自动衰减 |
| Semantic | 跨会话     | LongTermStore + 向量索引 | 事实/偏好/领域知识；不自动删除，只影响排序 |

语义检索：Episodic/Semantic 写入时同步进向量索引（L2），查询 =
混合重排（向量余弦 + BM25 词面分）× 遗忘曲线可检索度 exp(-间隔天数/stability)；
命中即强化（stability 翻倍、刷新 last_access）——越常复用的知识越不容易被遗忘。

decay()：遗忘曲线清理通道。低于阈值的 episodic 记录物理删除（遗忘），
semantic 不删除只降权——"经验会过时，事实不该因为没人提就消失"。
"""

from __future__ import annotations

import datetime
import math
import threading
from collections import deque

from src.harness.memory.long_term import (KIND_EPISODIC, KIND_SEMANTIC,
                                          LongTermStore, MemoryRecord)
from src.harness.memory.vector_store import VectorIndex, build_index
from src.harness.skills.recall import tokenize

WORKING_MAX_ITEMS = 50

# 混合检索：向量余弦为主，BM25 词面分加权补足（哈希嵌入是词面级的，
# 两者互补：向量管"部分改述仍相似"，BM25 管"稀有主题词必须命中"——
# IDF 让"任务/预算"这类到处出现的域词自动降权，不稀释判别力）
LEXICAL_WEIGHT = 0.5
BM25_K1 = 1.5
BM25_B = 0.75


def _bm25_norm(query: str, contents: list[str]) -> list[float]:
    """候选集内 BM25 打分并按最大值归一化到 [0,1]。

    IDF 在候选集上统计——候选只有几十条，够区分"这条改述里哪个词稀有"。
    """
    q_tokens = tokenize(query)
    if not q_tokens or not contents:
        return [0.0] * len(contents)
    docs_tokens = [tokenize(c) for c in contents]
    n_docs = len(docs_tokens)
    avgdl = (sum(len(d) for d in docs_tokens) / n_docs) or 1.0
    df: dict[str, int] = {}
    for d in docs_tokens:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    scores: list[float] = []
    for d in docs_tokens:
        tf: dict[str, int] = {}
        for t in d:
            tf[t] = tf.get(t, 0) + 1
        dl = len(d) or 1
        s = 0.0
        for t in q_tokens:
            if t in tf:
                idf = math.log((n_docs - df[t] + 0.5) / (df[t] + 0.5) + 1.0)
                s += idf * tf[t] * (BM25_K1 + 1) / \
                    (tf[t] + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl))
        scores.append(s)
    top = max(scores) or 1.0
    return [s / top for s in scores]


def _hybrid_rerank(query: str, hits: list[dict], resolve, top_k: int,
                   now=None) -> list[MemoryRecord]:
    """混合重排：最终分 = (向量余弦 + BM25 归一分×权重) × 遗忘曲线可检索度。

    hits 是向量索引的粗召回（top_k×5 过召回，重排后取 top_k）；resolve(id)
    把 id 解析成记忆记录（层内缓存优先，回落存储）。BM25 在候选集内打分，
    IDF 自动压低"任务/预算"这类高频域词的权重。
    """
    candidates: list[tuple[float, MemoryRecord]] = []
    for hit in hits:
        rec = resolve(hit["id"])
        if rec is not None:
            candidates.append((hit["score"], rec))
    if not candidates:
        return []
    bm25 = _bm25_norm(query, [rec.content for _, rec in candidates])
    scored: list[tuple[float, MemoryRecord]] = []
    for (cos, rec), lex in zip(candidates, bm25):
        score = (cos + LEXICAL_WEIGHT * lex) * retrievability(rec, now)
        scored.append((score, rec))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [rec for _, rec in scored[:top_k]]


def _parse_iso(ts: str) -> datetime.datetime | None:
    if not ts:
        return None
    try:
        return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def retrievability(record: MemoryRecord, now: datetime.datetime | None = None) -> float:
    """Ebbinghaus 可检索度：exp(-间隔天数 / stability)，1.0 = 刚访问过。"""
    now = now or datetime.datetime.now()
    base = _parse_iso(record.last_access) or _parse_iso(record.created_at)
    if base is None:
        return 1.0
    days = max(0.0, (now - base).total_seconds() / 86400.0)
    return pow(2.718281828459045, -days / max(0.1, record.stability))


class WorkingMemory:
    """L0 会话工作记忆：有界先进先出，线程安全。"""

    def __init__(self, thread_id: str = "", max_items: int = WORKING_MAX_ITEMS):
        self.thread_id = thread_id
        self.max_items = max_items
        self._lock = threading.Lock()
        self._items: deque[dict] = deque(maxlen=max_items)

    def add(self, content: str, *, role: str = "system", kind: str = "note") -> dict:
        item = {"content": content, "role": role, "kind": kind,
                "at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
        with self._lock:
            self._items.append(item)
        return item

    def dump(self, limit: int | None = None) -> list[dict]:
        with self._lock:
            items = list(self._items)
        return items[-limit:] if limit else items

    def clear(self) -> int:
        with self._lock:
            n = len(self._items)
            self._items.clear()
            return n

    def __len__(self) -> int:
        return len(self._items)


class SemanticLayer:
    """L2 语义记忆 + 向量索引：写入即嵌入，查询按 相似度×可检索度 排序。"""

    def __init__(self, store: LongTermStore, index: VectorIndex,
                 *, decay_proof: bool = True):
        self.store = store
        self.index = index
        self.decay_proof = decay_proof   # True = 不因衰减删除，只降权
        self._records: dict[str, MemoryRecord] = {}

    def remember(self, content: str, *, source: str = "",
                 confidence: float = 1.0) -> MemoryRecord:
        rec = self.store.remember(KIND_SEMANTIC, content, source=source,
                                  confidence=confidence)
        self.index.add(rec.id, content, metadata={"kind": KIND_SEMANTIC})
        self._records[rec.id] = rec
        return rec

    def search(self, query: str, top_k: int = 3, *,
               now: datetime.datetime | None = None) -> list[MemoryRecord]:
        hits = self.index.search(query, top_k=top_k * 5)

        def resolve(record_id: str) -> MemoryRecord | None:
            rec = self._records.get(record_id) or self.store.get(record_id)
            if rec is not None:
                self._records[record_id] = rec
            return rec

        out = _hybrid_rerank(query, hits, resolve, top_k, now)
        for rec in out:
            rec.reinforce()
        self.store._flush()
        return out

    def forget(self, record_id: str) -> bool:
        self.index.remove(record_id)
        self._records.pop(record_id, None)
        return self.store.forget(record_id)


class EpisodicLayer:
    """L1 情节记忆：经验类记录，可被遗忘曲线清理。"""

    def __init__(self, store: LongTermStore, index: VectorIndex):
        self.store = store
        self.index = index
        self._records: dict[str, MemoryRecord] = {}

    def remember(self, content: str, *, source: str = "",
                 confidence: float = 1.0) -> MemoryRecord:
        rec = self.store.remember(KIND_EPISODIC, content, source=source,
                                  confidence=confidence)
        self.index.add(rec.id, content, metadata={"kind": KIND_EPISODIC})
        self._records[rec.id] = rec
        return rec

    def search(self, query: str, top_k: int = 3, *,
               now: datetime.datetime | None = None) -> list[MemoryRecord]:
        hits = self.index.search(query, top_k=top_k * 5)

        def resolve(record_id: str) -> MemoryRecord | None:
            rec = self._records.get(record_id) or self.store.get(record_id)
            if rec is not None:
                self._records[record_id] = rec
            return rec

        out = _hybrid_rerank(query, hits, resolve, top_k, now)
        for rec in out:
            rec.reinforce()
        self.store._flush()
        return out

    def decay(self, *, threshold: float = 0.05,
              now: datetime.datetime | None = None) -> list[str]:
        """遗忘清理：可检索度低于阈值的经验物理删除，返回被遗忘的 id。"""
        forgotten: list[str] = []
        for rec in list(self.store.list(kind=KIND_EPISODIC, include_expired=True)):
            if retrievability(rec, now) < threshold:
                self.index.remove(rec.id)
                self.store.forget(rec.id)
                self._records.pop(rec.id, None)
                forgotten.append(rec.id)
        return forgotten


class MemoryLayers:
    """三层记忆统一门面：remember/search_all/decay 一个入口。"""

    def __init__(self, *, store: LongTermStore | None = None,
                 index: VectorIndex | None = None):
        self.store = store or LongTermStore()
        self.index = index or build_index(backend="auto")
        self.working = WorkingMemory()
        self.episodic = EpisodicLayer(self.store, self.index)
        self.semantic = SemanticLayer(self.store, self.index)

    # ---- 写 ----
    def remember_semantic(self, content: str, **kwargs) -> MemoryRecord:
        return self.semantic.remember(content, **kwargs)

    def remember_episodic(self, content: str, **kwargs) -> MemoryRecord:
        return self.episodic.remember(content, **kwargs)

    def remember_working(self, content: str, **kwargs) -> dict:
        return self.working.add(content, **kwargs)

    # ---- 读 ----
    def search(self, query: str, top_k: int = 3) -> list[MemoryRecord]:
        """跨 L1/L2 语义检索（L0 工作记忆用 dump() 直接看）。"""
        merged = {r.id: r for r in
                  self.semantic.search(query, top_k=top_k) +
                  self.episodic.search(query, top_k=top_k)}
        return list(merged.values())[:top_k]

    def search_all(self, query: str, top_k: int = 3) -> dict:
        """分层返回，供 Provider 渲染时标注来源层。"""
        return {"working": self.working.dump(),
                "semantic": self.semantic.search(query, top_k=top_k),
                "episodic": self.episodic.search(query, top_k=top_k)}

    # ---- 遗忘曲线 ----
    def decay(self, *, threshold: float = 0.05,
              now: datetime.datetime | None = None) -> dict:
        forgotten = self.episodic.decay(threshold=threshold, now=now)
        return {"forgotten_episodic": forgotten,
                "semantic_kept": len(self.store.list(kind=KIND_SEMANTIC)),
                "backend": getattr(self.index, "backend", "unknown")}

    def snapshot(self) -> dict:
        return {"working": len(self.working),
                "episodic": len(self.store.list(kind="episodic")),
                "semantic": len(self.store.list(kind="semantic")),
                "vector_backend": getattr(self.index, "backend", "unknown"),
                "vector_count": self.index.count()}
