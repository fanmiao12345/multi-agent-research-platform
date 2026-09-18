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
    # 遗忘曲线字段（Ebbinghaus 式检索强度模型）：
    #   retrievability = exp(-间隔天数 / stability)，每次被召回 stability 翻倍（强化）
    stability: float = 1.0
    last_access: str = ""         # ISO；空 = 从未召回（用 created_at 兜底）

    def is_expired(self, now: str | None = None) -> bool:
        if not self.expires_at:
            return False
        return (now or time.strftime("%Y-%m-%dT%H:%M:%S")) > self.expires_at

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("id", "kind", "content", "source", "confidence",
                 "created_at", "updated_at", "expires_at",
                 "stability", "last_access")}

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryRecord":
        rec = cls(**{k: d.get(k, "") for k in
                     ("id", "kind", "content", "source", "confidence",
                      "created_at", "updated_at", "expires_at")})
        # 旧文件没有这两个字段时安静取默认值（向后兼容）
        rec.stability = float(d.get("stability", 1.0) or 1.0)
        rec.last_access = d.get("last_access", "")
        return rec

    def reinforce(self, factor: float = 2.0, cap: float = 365.0) -> None:
        """被召回一次：稳定性按因子增强（上限 cap 天），刷新最近访问时间。"""
        self.stability = min(cap, max(0.1, self.stability) * factor)
        self.last_access = _now()
        self.updated_at = self.last_access


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
    def search(self, query: str, kind: str | None = None, top_k: int = 5,
               *, reinforce: bool = True) -> list[MemoryRecord]:
        """检索并（默认）强化命中记录的遗忘曲线稳定性。"""
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
        out = [r for _, r in scored[:top_k]]
        if reinforce:
            for rec in out:
                rec.reinforce()
            self._flush()
        return out

    def summarize(self, kind: str | None = None) -> str:
        """把某类记忆折叠成一段摘要（episodic 的历史任务回顾用）。"""
        return summarize_text("\n".join(r.content for r in self.list(kind)), max_chars=1200)
