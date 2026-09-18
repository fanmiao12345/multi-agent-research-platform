# -*- coding: utf-8 -*-
"""
harness/memory/vector_store.py —— L2 向量索引（语义检索）

两层实现，同一套接口（add / search / remove / count / save / load）：
- SqliteVssIndex   SQLite + sqlite-vec/vss 扩展（需可选依赖 sqlite_vec 或 .dll/.so 扩展）
- HashingVectorIndex  零依赖回退：字符 n-gram 特定哈希（feature hashing）→ 归一化向量 → 余弦

build_index(backend="auto")：先试 sqlite-vec，装了就用它（真向量索引），
没装自动落回 HashingVectorIndex——功能不变、检索是词面级的，通过 .backend
如实暴露当前后端，绝不假装"已经用了 sqlite-vss"。

embed() 是确定性哈希嵌入：同文本必得同向量；中文按双字词、英文按整词切，
跟 skills.recall 的分词口径一致，保证 BM25 与向量两条检索可互相印证。
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

from src.harness.skills.recall import tokenize

EMBED_DIM = 256


def _hash64(token: str) -> int:
    """FNV-1a 64 位：零依赖、跨进程稳定（Python 内置 hash 每次启动会变，不能用）。"""
    h = 0xCBF29CE484222325
    for ch in token.encode("utf-8"):
        h ^= ch
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    """确定性哈希嵌入：词集合 → 稀疏计数 → L2 归一化。"""
    vec = [0.0] * dim
    for token in tokenize(text or ""):
        vec[_hash64(token) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class VectorIndex:
    """向量索引接口（L2 语义检索的最小契约）。"""

    backend = "base"

    def add(self, record_id: str, text: str, *, metadata: dict | None = None) -> None:
        raise NotImplementedError

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """返回 [{id, score, metadata}]，score 是余弦相似度，降序。"""
        raise NotImplementedError

    def remove(self, record_id: str) -> bool:
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError


class HashingVectorIndex(VectorIndex):
    """零依赖内存向量索引（默认后端）。"""

    backend = "hashing_cosine"

    def __init__(self, dim: int = EMBED_DIM):
        self.dim = dim
        self._vectors: dict[str, list[float]] = {}
        self._meta: dict[str, dict] = {}

    def add(self, record_id: str, text: str, *, metadata: dict | None = None) -> None:
        self._vectors[record_id] = embed(text, self.dim)
        self._meta[record_id] = dict(metadata or {})

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        qv = embed(query, self.dim)
        scored = [(rid, cosine(qv, vec)) for rid, vec in self._vectors.items()]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [{"id": rid, "score": round(s, 6), "metadata": self._meta.get(rid, {})}
                for rid, s in scored[:top_k] if s > 0]

    def remove(self, record_id: str) -> bool:
        existed = record_id in self._vectors
        self._vectors.pop(record_id, None)
        self._meta.pop(record_id, None)
        return existed

    def count(self) -> int:
        return len(self._vectors)

    # ---- 持久化（JSON 行格式，够 L2 用；大数据量换 sqlite-vss 后端）----
    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"dim": self.dim,
                                    "vectors": self._vectors,
                                    "meta": self._meta},
                                   ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "HashingVectorIndex":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        index = cls(dim=data.get("dim", EMBED_DIM))
        index._vectors = {k: v for k, v in data.get("vectors", {}).items()}
        index._meta = dict(data.get("meta", {}))
        return index


class SqliteVssIndex(VectorIndex):
    """SQLite + sqlite-vec/vss 扩展后端。

    需要环境里装了 sqlite_vec 包（或手动 enable_load_extension 加载 vss）。
    当前环境的扩展不可用时构造抛 RuntimeError，由 build_index(auto) 捕获后
    落回 HashingVectorIndex——调用方永远拿到一个可用的索引。
    """

    backend = "sqlite_vss"

    def __init__(self, path: str | Path = ":memory:", dim: int = EMBED_DIM):
        self.dim = dim
        self.conn = sqlite3.connect(str(path))
        self.conn.enable_load_extension(True)
        try:
            import sqlite_vec  # type: ignore  # 可选依赖
            sqlite_vec.load(self.conn)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"sqlite-vec/vss 扩展不可用（{e}）；用 build_index(backend='auto') "
                f"自动落回零依赖实现") from e
        self.conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0("
            f"record_id TEXT PRIMARY KEY, embedding FLOAT[{dim}])")
        self._meta: dict[str, dict] = {}

    def add(self, record_id: str, text: str, *, metadata: dict | None = None) -> None:
        vec = embed(text, self.dim)
        blob = json.dumps(vec)  # sqlite-vec 接受 JSON 文本向量
        self.conn.execute("INSERT OR REPLACE INTO vec_items(record_id, embedding) "
                          "VALUES (?, ?)", (record_id, blob))
        self._meta[record_id] = dict(metadata or {})
        self.conn.commit()

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        qv = json.dumps(embed(query, self.dim))
        rows = self.conn.execute(
            "SELECT record_id, distance FROM vec_items WHERE embedding MATCH ? "
            "ORDER BY distance LIMIT ?", (qv, top_k)).fetchall()
        # sqlite-vec MATCH 默认返回 L2 欧氏距离；本项目向量恒 L2 归一化，
        # 有精确换算 cos = 1 - L2²/2（否则分数会出现 -0.13 这类越界值）
        return [{"id": rid, "score": round(1.0 - float(dist) ** 2 / 2.0, 6),
                 "metadata": self._meta.get(rid, {})} for rid, dist in rows]

    def remove(self, record_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM vec_items WHERE record_id = ?",
                                (record_id,))
        self.conn.commit()
        self._meta.pop(record_id, None)
        return cur.rowcount > 0

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM vec_items").fetchone()[0]


def build_index(backend: str = "auto", *, path: str | Path = ":memory:",
                dim: int = EMBED_DIM) -> VectorIndex:
    """工厂：auto = 先试 sqlite-vss，失败落回零依赖哈希余弦。"""
    if backend in ("auto", "sqlite_vss"):
        try:
            return SqliteVssIndex(path=path, dim=dim)
        except RuntimeError:
            if backend == "sqlite_vss":
                raise
            return HashingVectorIndex(dim=dim)
    if backend == "hashing_cosine":
        return HashingVectorIndex(dim=dim)
    raise ValueError(f"未知向量后端 {backend}（可用：auto / sqlite_vss / hashing_cosine）")
