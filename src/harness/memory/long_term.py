# -*- coding: utf-8 -*-
"""
harness/memory/long_term.py —— Long-term Store（DEV_PLAN F2-F3 / 步骤 67-70）

跨 Thread 保存：remember / search / update / forget。
记忆类型（kind）：
    semantic    事实 / 用户偏好 / 领域知识
    episodic    历史任务 / 失败经验 / 成功路径
    procedural  工作规则 / 操作方法 / 成功策略

持久化：JSON 文件（默认 workspaces/memory_store.json，随运行目录忽略提交）。
记录带 source / confidence / created_at / updated_at / expires_at（F4 字段）。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from src.harness.context.compressors import summarize_text

KIND_SEMANTIC = "semantic"
KIND_EPISODIC = "episodic"
KIND_PROCEDURAL = "procedural"
ALL_KINDS = (KIND_SEMANTIC, KIND_EPISODIC, KIND_PROCEDURAL)


@dataclass
class MemoryRecord:
    id: str
    kind: str
    content: str
    source: str = ""
    confidence: float = 1.0
    created_at: str = ""
    updated_at: str = ""
    expires_at: str = ""          # ISO；空 = 永不过期

    def is_expired(self, now: str | None = None) -> bool:
        if not self.expires_at:
            return False
        return (now or time.strftime("%Y-%m-%dT%H:%M:%S")) > self.expires_at

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("id", "kind", "content", "source", "confidence",
                 "created_at", "updated_at", "expires_at")}

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryRecord":
        return cls(**{k: d.get(k, "") for k in
                      ("id", "kind", "content", "source", "confidence",
                       "created_at", "updated_at", "expires_at")})


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class LongTermStore:
    def __init__(self, path: str | Path | None = None):
        from config.settings import PROJECT_ROOT

        self.path = Path(path) if path else PROJECT_ROOT / "workspaces" / "memory_store.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[MemoryRecord] = self._load()

    def _load(self) -> list[MemoryRecord]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [MemoryRecord.from_dict(r) for r in data]
        except Exception:
            return []

    def _flush(self) -> None:
        self.path.write_text(
            json.dumps([r.to_dict() for r in self._records],
                       ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- CRUD ----
    def remember(self, kind: str, content: str, *, source: str = "",
                 confidence: float = 1.0, expires_at: str = "") -> MemoryRecord:
        if kind not in ALL_KINDS:
            raise ValueError(f"kind 必须是 {ALL_KINDS} 之一，收到 {kind}")
        now = _now()
        rec = MemoryRecord(id=uuid.uuid4().hex[:8], kind=kind, content=content,
                           source=source, confidence=confidence,
                           created_at=now, updated_at=now, expires_at=expires_at)
        self._records.append(rec)
        self._flush()
        return rec

    def get(self, record_id: str) -> MemoryRecord | None:
        return next((r for r in self._records if r.id == record_id), None)

    def update(self, record_id: str, **changes) -> MemoryRecord | None:
        rec = self.get(record_id)
        if rec is None:
            return None
        for key in ("content", "confidence", "expires_at", "source"):
            if key in changes:
                setattr(rec, key, changes[key])
        rec.updated_at = _now()
        self._flush()
        return rec

    def forget(self, record_id: str) -> bool:
        before = len(self._records)
        self._records = [r for r in self._records if r.id != record_id]
        if len(self._records) != before:
            self._flush()
            return True
        return False

    def list(self, kind: str | None = None, include_expired: bool = False) -> list[MemoryRecord]:
        out = [r for r in self._records if kind is None or r.kind == kind]
        if not include_expired:
            out = [r for r in out if not r.is_expired()]
        return out

    # ---- 简单检索（子串 + kind 过滤；向量版由 knowledge/retrieval 提供）----
    def search(self, query: str, kind: str | None = None, top_k: int = 5) -> list[MemoryRecord]:
        scored = []
        for rec in self.list(kind=kind):
            score = 0
            q = query.lower()
            if q in rec.content.lower():
                score += 3
            for tok in (k for k in query if k.strip()):
                if tok in rec.content:
                    score += 1
            if score:
                scored.append((score, rec))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:top_k]]

    def summarize(self, kind: str | None = None) -> str:
        """把某类记忆折叠成一段摘要（episodic 的历史任务回顾用）。"""
        return summarize_text("\n".join(r.content for r in self.list(kind)), max_chars=1200)
