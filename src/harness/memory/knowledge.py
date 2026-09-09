# -*- coding: utf-8 -*-
"""
harness/memory/knowledge.py —— Knowledge / RAG-lite（DEV_PLAN F5 / 步骤 72）

知识源：knowledge/*.md（项目根目录，可配 KNOWLEDGE_DIR）。
第一版 BM25-lite 检索（复用 skills.recall 的分词与打分）；embedding 后补。
"""

from __future__ import annotations

import os
from pathlib import Path

from src.harness.skills.recall import score_doc, tokenize

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
KNOWLEDGE_DIR = Path(os.environ.get("KNOWLEDGE_DIR", PROJECT_ROOT / "knowledge"))


def load_documents(root=KNOWLEDGE_DIR) -> list[dict]:
    docs = []
    if root.is_dir():
        for path in sorted(root.glob("*.md")):
            if path.name.startswith("_"):
                continue
            text = path.read_text(encoding="utf-8")
            # 取正文（跳过 YAML frontmatter 若存在）
            if text.startswith("---"):
                parts = text.split("---", 2)
                text = parts[2] if len(parts) == 3 else text
            docs.append({"name": path.stem, "content": text.strip(),
                         "tokens": tokenize(text)})
    return docs


def retrieve(question: str, root=KNOWLEDGE_DIR, top_k: int = 3) -> list[dict]:
    """返回 [{name, score, matched, snippet}]，按分数降序。"""
    qt = tokenize(question)
    if not qt:
        return []
    docs = load_documents(root)
    scored = []
    for doc in docs:
        base, matched = score_doc(qt, doc["tokens"])
        if base > 0:
            scored.append({"name": doc["name"], "score": base,
                           "matched": matched,
                           "snippet": doc["content"][:200]})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
