# -*- coding: utf-8 -*-
"""
application/pipeline/evidence.py —— 证据提取与持久化（S3-04）

流程顺序：
1. 逐来源调用模型给出 (fact, tag, quote)；
2. 程序先校验：quote 必须逐字存在于来源全文（找到偏移），tag ∈ {F,I,U}；
   校验失败的条目丢弃并记 issue（绝不让未定位摘录进入证据库）；
3. 定位：quote 起点所在段落（与 storage.sources.split_segments 同一口径）；
4. 稳定编号 E-001…写入 job_dir/evidence.json（只追加，修订不覆盖）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from src.application.pipeline.model import EvidenceItem, StageError
from src.application.pipeline.prompts import build_evidence_messages
from src.harness.model_gateway import model_call
from src.harness.run_store import write_json
from src.harness.storage.sources import split_segments
from src.harness.structured import extract_json

MAX_QUOTE = 400
SOURCE_TEXT_BUDGET = 40_000   # 单来源喂给模型的字符预算（S3-07：按窗口控制长度）

_ID_RE = re.compile(r"^E-\d{3}$")
# 兼容 [E-001] 及全角/括号变体（【E-001】、（E-001）…）→ 统一按 E-001 计
_CITE_RE = re.compile(r"[\[【（(]\s*(E-\d{3})\s*[\]】）)]")


class EvidenceStore:
    """job_dir/evidence.json：证据是任务的正式中间产物，以文件为权威。"""

    def __init__(self, job_dir: Path):
        self.path = Path(job_dir) / "evidence.json"

    def save_all(self, items: list[EvidenceItem]) -> None:
        write_json(self.path, {"schema_version": 1,
                               "items": [i.as_dict() for i in items]})

    def load_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            with self.path.open(encoding="utf-8") as f:
                data = json.load(f)
            return data.get("items", []) if isinstance(data, dict) else []
        except Exception:
            raise StageError("evidence", "evidence.json 无法解析，拒绝覆盖未知历史证据") from None

    def ids(self) -> set[str]:
        return {item["evidence_id"] for item in self.load_all()}

    @staticmethod
    def is_id(token: str) -> bool:
        return bool(_ID_RE.fullmatch(token or ""))


def make_id(sequence: int) -> str:
    return f"E-{sequence:03d}"


def validate_id(token: str) -> bool:
    return EvidenceStore.is_id(token)


def collect_citations(text: str) -> list[str]:
    """正文中的 [E-xxx] 引用标记（保持出现顺序，含重复）。"""
    return _CITE_RE.findall(text or "")


def build_evidence_block_for_source(source: dict) -> str:
    """把一份来源（全文可能很大）裁剪到单次调用预算，并保留超限说明。"""
    text = source.get("text") or ""
    truncated = len(text) > SOURCE_TEXT_BUDGET
    shown = text[:SOURCE_TEXT_BUDGET]
    if truncated:
        shown += "\n\n[……来源过长，本次调用只包含前段；未提供的后段内容禁止猜测]"
    return {"source_id": source.get("source_id"), "title": source.get("title", ""),
            "display": source.get("display", source.get("source_id", "?")),
            "text": shown, "truncated": truncated}


def extract_source_evidence(llm, goal: str, source: dict) -> tuple[list[EvidenceItem], list[dict]]:
    """对单个来源调用证据提取器并做程序校验；返回 (通过项, issue 列表)。

    解析失败（含截断）会自动重试一次并要求只输出 JSON，仍失败抛 StageError。
    """
    issues: list[dict] = []
    data = None
    for attempt in (1, 2):
        messages = build_evidence_messages(goal, build_evidence_block_for_source(source))
        if attempt == 2:
            messages = messages[:1] + [{
                "role": "system",
                "content": "上一次输出无法解析为 JSON（可能被截断）。这次只输出一个完整、"
                           "合法的 JSON 对象，items 数量宁少勿多（最多6条），不要任何解释。"}] \
                + messages[1:]
        reply = model_call(llm, messages, purpose="evidence_extract", role="evidence")
        data = extract_json(reply.content or "")
        if data and isinstance(data.get("items"), list):
            break
    if not data or not isinstance(data.get("items"), list):
        raise StageError("evidence", "证据提取输出不是合法 JSON 对象（items 列表缺失）")
    items: list[EvidenceItem] = []
    full_text = source.get("text") or ""
    segments = source.get("segments") or split_segments(full_text)
    for raw in data["items"]:
        if not isinstance(raw, dict):
            issues.append({"severity": "warn", "code": "format",
                           "message": "证据条目不是对象，已跳过"})
            continue
        fact = (raw.get("fact") or "").strip()
        quote = (raw.get("quote") or "").strip()
        try:
            tag = EvidenceItem.validate_tag(raw.get("tag"))
        except StageError as e:
            issues.append({"severity": "warn", "code": "format", "message": str(e)})
            continue
        if not fact:
            issues.append({"severity": "warn", "code": "format", "message": "证据 fact 为空，已跳过"})
            continue
        if not quote or len(quote) > MAX_QUOTE:
            issues.append({"severity": "warn", "code": "format",
                           "message": f"摘录为空或超过 {MAX_QUOTE} 字符，已跳过"})
            continue
        start = full_text.find(quote)
        if start < 0:
            issues.append({"severity": "error", "code": "citation",
                           "message": f"摘录未在来源 {source.get('source_id')} 中逐字找到，"
                                      "按不可定位处理，未入库"})
            continue
        end = start + len(quote)
        locator = {}
        for seg in segments:
            if seg["start"] <= start < seg["end"]:
                locator = {"heading": seg.get("heading", ""),
                           "paragraph": seg.get("paragraph", -1),
                           "start": seg["start"], "end": seg["end"]}
                if seg.get("page") is not None:
                    locator["page"] = seg["page"]
                break
        items.append(EvidenceItem(evidence_id="", source_id=source.get("source_id", ""),
                                  fact=fact, tag=tag, quote=quote,
                                  start=start, end=end, locator=locator))
    return items, issues
