# -*- coding: utf-8 -*-
"""
harness/storage/sources.py —— 来源登记、导入与去重（S2-01/S2-05/S2-06 本地部分）

一次导入的边界：
- kind=file：读用户显式给出的本地 TXT/Markdown/PDF 文件（只读原文，从不回写）；
- kind=paste：粘贴文本。
- 每个来源登记 id、获取时间、内容哈希、原始地址与最终存储位置；
- 结果分类：ok / partial / duplicate / empty / unsupported / too_large / read_failed，
  所有分类都登记进索引，供页面与评测看到明确结果；
- 全文按格式保存到 job_dir/sources/ 下，段落定位（标题+段落号+字符偏移）写入
  同名 .meta.json；sources.json 保存来源清单索引（不含段落明细）。

限制（实用化计划 1.3 拟定值，超限明确拒绝，不静默截断）：
MAX_SOURCES=20、单来源 2MB、任务累计存储 10MB。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.harness.run_store import write_json
from src.harness.storage.paths import canonical, ensure_relative_name, resolve_under

MAX_SOURCES = 20
MAX_BYTES_PER_SOURCE = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024

# 明确不支持的扩展名：给出可操作提示，而不是按二进制乱码导入。
_BLOCKED_EXTS = {
    "doc": "DOC 旧格式不受支持，请另存为 .txt/.md 或文本粘贴",
    "docx": "DOCX 不受首批支持，请另存为 .txt/.md 或文本粘贴",
    "xls": "表格格式不受支持", "xlsx": "表格格式不受支持",
    "ppt": "演示文稿不受支持", "pptx": "演示文稿不受支持",
    "png": "图片资料不受支持", "jpg": "图片资料不受支持", "jpeg": "图片资料不受支持",
    "gif": "图片资料不受支持", "webp": "图片资料不受支持",
    "zip": "压缩包不受支持，请先解压出文本", "rar": "压缩包不受支持，请先解压出文本",
    "7z": "压缩包不受支持，请先解压出文本", "gz": "压缩包不受支持，请先解压出文本",
    "exe": "可执行文件不受支持", "dll": "二进制文件不受支持", "bin": "二进制文件不受支持",
}
_TEXT_EXTS = {"txt", "text", "md", "markdown"}
_HEADING = re.compile(r"^ {0,3}#{1,6}\s+\S", re.MULTILINE)
_BINARY_SNIFF_BYTES = 1024

# 抓取器状态 → 存储层分类（B4；见 SourceStore.add_url）
_FETCH_TO_STORAGE = {
    "http_error": "read_failed", "timeout": "read_failed",
    "network_error": "read_failed", "redirect_limit": "read_failed",
    "unsupported_type": "unsupported",
    "too_large": "too_large", "ok": "ok", "partial": "partial", "empty": "empty",
}


@dataclass
class SourceRecord:
    source_id: str
    kind: str                      # file | paste | url
    display: str                   # 原名 / “粘贴文本 N” / 网页标题或URL
    original_address: str          # 原文件规范路径 / "paste:N" / 请求的原始URL
    title: str
    content_format: str            # txt | md
    status: str                    # ok|partial|duplicate|empty|unsupported|too_large|read_failed|withdrawn|expired|superseded
    status_message: str = ""
    byte_size: int = 0
    content_hash: str = ""
    normalized_hash: str = ""
    captured_at: str = ""          # 导入时间（本机文件/网页的“获取时间”）
    published_date: str = "unknown"  # 本地文件/网页发布日期未知，不冒充
    duplicate_of: str | None = None
    file_name: str = ""            # 相对 job 目录的全文文件；失败来源为空
    encoding: str = ""
    segment_count: int = 0
    segments: list[dict] = field(default_factory=list)  # 仅写入 <id>.meta.json
    # URL 来源（S2-05）：最终URL（重定向后）、HTTP状态、内容类型；非URL来源留空
    final_url: str = ""
    http_status: int | None = None
    content_type: str = ""
    # D3-04/05：PDF 页范围、来源版本与共享来源血缘
    page_count: int = 0
    source_version: int = 1
    root_source_id: str = ""
    source_job_id: str = ""
    retrieved_at: str = ""
    withdrawn_at: str = ""
    withdrawal_reason: str = ""
    expires_at: str = ""
    superseded_by: str = ""
    supersedes: str = ""

    def meta(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _new_source_id() -> str:
    return "src_" + uuid.uuid4().hex[:10]


def _sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _normalize(text: str) -> str:
    """空白折叠：只用来识别“转载/同文”等实质重复，不代表展示。"""
    return " ".join(text.split())


def _ext_of(path: str | Path) -> str:
    return Path(path).suffix.lower().lstrip(".")


def detect_format(text: str, ext: str = "") -> str:
    if ext in ("md", "markdown") or _HEADING.search(text):
        return "md"
    return "txt"


def split_segments(text: str, *, max_heading=120,
                   page_spans: list[dict] | None = None) -> list[dict]:
    """按标题与空行切分，返回可定位原文的段落。

    每条包含 heading/paragraph/start/end；PDF 来源额外包含 page 页码，
    非 PDF 来源 page=None。标题行本身不算段落。
    """
    segments = []
    current_heading = ""
    paragraph_index = 0
    buf_start = None
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if _HEADING.match(line):
            if buf_start is not None:  # 标题前没有空行时先收拢上一段落
                segments.append({"heading": current_heading, "paragraph": paragraph_index,
                                 "start": buf_start, "end": offset})
                paragraph_index += 1
                buf_start = None
            current_heading = stripped[:max_heading]
            offset += len(line)
            continue
        if buf_start is None and stripped:
            buf_start = offset
        elif buf_start is not None and not stripped:
            segments.append({"heading": current_heading, "paragraph": paragraph_index,
                             "start": buf_start, "end": offset})
            paragraph_index += 1
            buf_start = None
        offset += len(line)
    if buf_start is not None:
        segments.append({"heading": current_heading, "paragraph": paragraph_index,
                         "start": buf_start, "end": offset})
    if page_spans:
        for segment in segments:
            segment["page"] = _page_for_offset(segment["start"], page_spans)
    else:
        for segment in segments:
            segment["page"] = None
    return segments


def _page_for_offset(offset: int, page_spans: list[dict]) -> int | None:
    for span in page_spans:
        start = int(span.get("start") or 0)
        end = int(span.get("end") or start)
        if start <= offset < end or (start == end == offset):
            return int(span.get("page") or 0) or None
    return None


def _title_of(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^ {0,3}#{1,6}\s+", "", s)
        return s[:120]
    return ""


def _decode_bytes(data: bytes) -> tuple[str, str, str, str]:
    """返回 (text, encoding, status, message)。UTF-8→GB18030→替换式降级。"""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding), encoding, "ok", ""
        except UnicodeDecodeError:
            continue
    text = data.decode("utf-8", errors="replace")
    bad = text.count("\ufffd")
    return (text, "utf-8-replace", "partial",
            f"{bad} 处字节无法按 UTF-8/GB18030 解码，已用替换字符保留其余正文")


def _looks_binary(data: bytes) -> bool:
    sample = data[:_BINARY_SNIFF_BYTES]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    non_text = sum(1 for b in sample if b < 9 or 13 < b < 32)
    return non_text / len(sample) > 0.3


def _atomic_write_text(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".src-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


class SourceImportError(ValueError):
    """导入结果里没有可用来源：任务必须在此停止并明确提示。"""


class SourceStore:
    """一个 job 目录内的来源存储与索引。job_dir/sources/ + sources.json。"""

    def __init__(self, job_dir: Path):
        self.job_dir = Path(job_dir)
        self.directory = resolve_under(job_dir, "sources")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_path = resolve_under(job_dir, "sources.json")

    # ---- 索引读写 -------------------------------------------------------
    def load_index(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        try:
            with self.index_path.open(encoding="utf-8") as f:
                data = json.load(f)
            return data.get("sources", []) if isinstance(data, dict) else []
        except Exception:
            raise SourceImportError("sources.json 无法解析，拒绝覆盖未知历史索引") from None

    @staticmethod
    def _index_record(meta: dict) -> dict:
        return {k: meta.get(k) for k in (
            "source_id", "kind", "display", "original_address", "title",
            "content_format", "status", "status_message", "byte_size",
            "content_hash", "normalized_hash", "captured_at", "published_date",
            "duplicate_of", "file_name", "encoding", "segment_count",
            "final_url", "http_status", "content_type", "page_count",
            "source_version", "root_source_id", "source_job_id", "retrieved_at",
            "withdrawn_at", "withdrawal_reason", "expires_at", "superseded_by",
            "supersedes")}

    def _replace_record(self, record: SourceRecord) -> None:
        """覆盖同一来源的 meta 与索引摘要；用于版本、撤回和过期状态。"""
        meta = record.meta()
        write_json(self.directory / f"{record.source_id}.meta.json", meta)
        index = self.load_index()
        found = False
        for position, item in enumerate(index):
            if item.get("source_id") == record.source_id:
                index[position] = self._index_record(meta)
                found = True
                break
        if not found:
            raise SourceImportError(f"来源不存在：{record.source_id}")
        write_json(self.index_path, {"schema_version": 1, "sources": index})

    def _commit(self, record: SourceRecord, text: str | None) -> None:
        """持久化一个来源：有全文先原子写全文，再写 meta 与索引。"""
        if text is not None:
            safe_name = ensure_relative_name(record.file_name)
            text_path = self.directory / safe_name
            _atomic_write_text(text_path, text)
            record.file_name = f"sources/{safe_name}"
        meta = record.meta()
        write_json(self.directory / f"{record.source_id}.meta.json", meta)
        index = self.load_index()
        if any(item.get("source_id") == record.source_id for item in index):
            raise SourceImportError(f"来源 id 冲突：{record.source_id}")
        index.append(self._index_record(meta))
        write_json(self.index_path, {"schema_version": 1, "sources": index})

    # ---- 重复识别 -------------------------------------------------------
    def find_duplicate(self, content_hash: str, normalized: str) -> tuple[str, str] | None:
        """返回 (source_id, 说明)；同哈希或实质同文（空白折叠一致）视为重复。"""
        for record in self.load_index():
            if record.get("status") != "ok":
                continue
            if record.get("content_hash") == content_hash:
                return record["source_id"], "与现有来源内容完全相同"
            if record.get("normalized_hash") == normalized:
                return record["source_id"], "与现有来源实质相同（仅空白/换行差异），视为转载"
        return None

    # ---- 导入入口 -------------------------------------------------------
    def add_paste(self, text: str, *, display_index: int) -> SourceRecord:
        if not text.strip():
            record = SourceRecord(
                source_id=_new_source_id(), kind="paste",
                display=f"粘贴文本 {display_index}",
                original_address=f"paste:{display_index}", title="",
                content_format="txt", status="empty",
                status_message="粘贴内容为空", captured_at=_now(),
                published_date="unknown")
            self._commit(record, None)
            return record
        return self._add(text=text, kind="paste",
                         display=f"粘贴文本 {display_index}",
                         original_address=f"paste:{display_index}")

    def add_file(self, path: str | Path) -> SourceRecord:
        original = canonical(path)
        display = Path(path).name or "文件"

        def failed(message: str) -> SourceRecord:
            # 失败来源也要登记进索引，页面才能显示“读取失败/不支持”
            now = _now()
            return SourceRecord(
                source_id=_new_source_id(), kind="file", display=display,
                original_address=str(original), title="", content_format="txt",
                status="read_failed", status_message=message,
                captured_at=now, retrieved_at=now)

        if not original.exists():
            record = failed(f"文件不存在或不可读：{display}")
            self._commit(record, None)
            return record
        if not original.is_file():
            record = failed(f"不是普通文件：{display}")
            self._commit(record, None)
            return record
        ext = _ext_of(original)
        if ext in _BLOCKED_EXTS:
            record = SourceRecord(source_id=_new_source_id(), kind="file",
                                  display=display, original_address=str(original),
                                  title="", content_format="txt", status="unsupported",
                                  status_message=_BLOCKED_EXTS[ext])
            self._commit(record, None)
            return record
        try:
            data = original.read_bytes()
        except OSError as e:
            record = failed(f"读取失败：{type(e).__name__}")
            self._commit(record, None)
            return record
        if len(data) > MAX_BYTES_PER_SOURCE:
            record = SourceRecord(source_id=_new_source_id(), kind="file",
                                  display=display, original_address=str(original),
                                  title="", content_format="txt", status="too_large",
                                  status_message=(f"超过单来源 "
                                                  f"{MAX_BYTES_PER_SOURCE // (1024 * 1024)}MB 上限，"
                                                  "请拆分或缩小范围，不会静默截断"))
            self._commit(record, None)
            return record
        if ext == "pdf":
            from src.harness.ingest.pdf_extract import extract_pdf
            extracted = extract_pdf(data)
            pdf_status = extracted.get("status") or "read_failed"
            if pdf_status not in ("ok", "partial"):
                record = SourceRecord(
                    source_id=_new_source_id(), kind="file", display=display,
                    original_address=str(original), title="", content_format="txt",
                    status=pdf_status, status_message=extracted.get("message") or "PDF 不可读取",
                    page_count=int(extracted.get("page_count") or 0),
                    captured_at=_now(), retrieved_at=_now())
                self._commit(record, None)
                return record
            return self._add(
                text=extracted.get("text") or "", kind="file", display=display,
                original_address=str(original), ext="txt", encoding="utf-8",
                base_status=pdf_status, base_message=extracted.get("message") or "",
                page_count=int(extracted.get("page_count") or 0),
                page_spans=extracted.get("pages") or [])
        # 二进制嗅探先于文本解码：NUL/控制字符占比高的“文本扩展名”文件仍是二进制
        if _looks_binary(data):
            record = SourceRecord(source_id=_new_source_id(), kind="file",
                                  display=display, original_address=str(original),
                                  title="", content_format="txt", status="unsupported",
                                  status_message="检测为二进制/非文本文件，不受支持")
            self._commit(record, None)
            return record
        if ext not in _TEXT_EXTS:
            ext = ""  # 无扩展名/未知文本扩展名：按内容探测
        text, encoding, status, message = _decode_bytes(data)
        if not text.strip():
            status, message = "empty", "文件没有可提取的文本内容"
        return self._add(text=text, kind="file", display=display,
                         original_address=str(original), ext=ext,
                         encoding=encoding, base_status=status, base_message=message)

    def _merge_url_fields(self, record: SourceRecord, url_fields: dict | None) -> SourceRecord:
        if url_fields:
            for key in ("final_url", "http_status", "content_type", "title"):
                if key in url_fields:
                    setattr(record, key, url_fields[key])
        return record

    def add_url(self, *, url: str, text: str | None = None, title: str = "",
                final_url: str = "", http_status: int | None = None,
                content_type: str = "", encoding: str = "", status: str = "ok",
                message: str = "", content_format: str | None = None) -> SourceRecord:
        """登记一个网页来源（S2-02/05 URL 侧）。

        url=原始请求URL；final_url=重定向后的最终URL（无则同 url）；
        status 为 fetcher/extractor 给出的分类，这里映射为存储层分类：
        http_error/timeout/network_error/redirect_limit→read_failed，
        unsupported_type→unsupported，too_large/ok/partial/empty 原样保留。
        ok/partial 需要 text（已提取正文）。
        """
        storage_status = _FETCH_TO_STORAGE.get(status, status)
        url_fields = {"final_url": final_url or url,
                      "http_status": http_status, "content_type": content_type or "",
                      "title": (title or "")[:200]}
        display = (title or final_url or url)[:200] or url[:200]

        def fail_record(reason_status: str, reason_message: str) -> SourceRecord:
            record = SourceRecord(source_id=_new_source_id(), kind="url", display=display,
                                  original_address=url, title=url_fields["title"],
                                  content_format="txt", status=reason_status,
                                  status_message=reason_message, captured_at=_now(),
                                  published_date="unknown")
            self._merge_url_fields(record, url_fields)
            self._commit(record, None)
            return record

        if storage_status not in ("ok", "partial"):
            return fail_record(storage_status, message or status)
        if text is None or not text.strip():
            return fail_record("empty", "网页没有可提取的文本内容")
        if len(text.encode("utf-8")) > MAX_BYTES_PER_SOURCE:
            return fail_record("too_large",
                               f"提取正文超过 {MAX_BYTES_PER_SOURCE // (1024 * 1024)}MB 上限，"
                               "不会静默截断")
        return self._add(text=text, kind="url", display=display,
                         original_address=url, ext="md" if content_format == "md" else "",
                         encoding=encoding or "utf-8", base_status=storage_status,
                         base_message=message, url_fields=url_fields)

    def _add(self, text: str, *, kind: str, display: str, original_address: str,
             ext: str = "", encoding: str = "utf-8",
             base_status: str = "ok", base_message: str = "",
             url_fields: dict | None = None, page_count: int = 0,
             page_spans: list[dict] | None = None) -> SourceRecord:
        byte_size = len(text.encode("utf-8"))
        content_format = detect_format(text, ext)
        segments = split_segments(text, page_spans=page_spans)
        now = _now()
        if base_status not in ("ok", "partial"):
            record = SourceRecord(
                source_id=_new_source_id(), kind=kind, display=display,
                original_address=original_address, title=_title_of(text),
                content_format=content_format, status=base_status,
                status_message=base_message, byte_size=0, captured_at=now,
                retrieved_at=now, published_date="unknown", encoding=encoding,
                page_count=page_count)
            self._merge_url_fields(record, url_fields)
            self._commit(record, None)
            return record
        if byte_size > MAX_BYTES_PER_SOURCE:
            record = SourceRecord(
                source_id=_new_source_id(), kind=kind, display=display,
                original_address=original_address, title="",
                content_format="txt", status="too_large",
                status_message=(f"超过单来源 {MAX_BYTES_PER_SOURCE // (1024 * 1024)}MB 上限，"
                                "请拆分或缩小范围，不会静默截断"), captured_at=now,
                retrieved_at=now, published_date="unknown", page_count=page_count)
            self._merge_url_fields(record, url_fields)
            self._commit(record, None)
            return record
        record = SourceRecord(
            source_id=_new_source_id(), kind=kind, display=display,
            original_address=original_address, title=_title_of(text),
            content_format=content_format, status=base_status,
            status_message=base_message, byte_size=byte_size,
            content_hash=_sha256(text), normalized_hash=_sha256(_normalize(text)),
            captured_at=now, retrieved_at=now, published_date="unknown",
            encoding=encoding, page_count=page_count,
            segment_count=len(segments), segments=segments)
        self._merge_url_fields(record, url_fields)
        duplicate = self.find_duplicate(record.content_hash, record.normalized_hash)
        if duplicate:
            record.status = "duplicate"
            record.status_message = duplicate[1]
            record.duplicate_of = duplicate[0]
            record.segments = []
            record.segment_count = 0
            self._commit(record, None)
            return record
        file_name = f"{record.source_id}.{content_format}"
        record.file_name = file_name  # _commit 校验裸文件名后补 sources/ 前缀
        self._commit(record, text)
        return record

    # ---- D3-05：版本、撤回、过期与共享血缘 -------------------------------
    def _load_record(self, source_id: str) -> SourceRecord:
        safe_name = ensure_relative_name(source_id)
        path = self.directory / f"{safe_name}.meta.json"
        if not path.exists():
            raise SourceImportError(f"来源不存在：{source_id}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            fields = SourceRecord.__dataclass_fields__
            return SourceRecord(**{k: v for k, v in data.items() if k in fields})
        except Exception:  # noqa: BLE001
            raise SourceImportError(f"来源元数据无法解析：{source_id}") from None

    def usable_texts(self) -> list[tuple[dict, str]]:
        """返回可用来源的索引记录与全文；消费者不得把失败/撤回来源当证据。"""
        result = []
        for record in self.load_index():
            if record.get("status") not in ("ok", "partial") or not record.get("file_name"):
                continue
            text = self.full_text(record["source_id"])
            if text is not None:
                result.append((record, text))
        return result

    def link_source_library(self, library: "SourceStore", *, source_job_id: str = "") -> int:
        """按正文哈希把本地来源关联到根共享库；不复制全文，不改变原始证据。"""
        by_hash = {}
        for record in library.load_index():
            if record.get("content_hash"):
                by_hash[record["content_hash"]] = record
            if record.get("normalized_hash"):
                by_hash.setdefault(record["normalized_hash"], record)
        linked = 0
        for item in self.load_index():
            if item.get("status") not in ("ok", "partial"):
                continue
            root = (by_hash.get(item.get("content_hash"))
                    or by_hash.get(item.get("normalized_hash")))
            if not root:
                continue
            record = self._load_record(item["source_id"])
            record.root_source_id = root.get("source_id") or ""
            record.source_version = int(root.get("source_version") or 1)
            record.source_job_id = source_job_id or root.get("source_job_id") or ""
            record.retrieved_at = (root.get("retrieved_at")
                                   or root.get("captured_at") or record.retrieved_at)
            self._replace_record(record)
            linked += 1
        return linked

    def withdraw(self, source_id: str, reason: str) -> dict:
        """撤回来源并返回其下游引用；状态改为 withdrawn，后续不得继续作为证据。"""
        record = self._load_record(source_id)
        dependents = self.find_dependents(source_id)
        if record.status not in ("withdrawn", "expired"):
            record.status = "withdrawn"
            record.withdrawn_at = _now()
            record.withdrawal_reason = (reason or "用户撤回来源").strip()
            record.status_message = f"来源已撤回：{record.withdrawal_reason}"
            self._replace_record(record)
        return {"source": record.meta(), "dependents": dependents}

    def set_expiry(self, source_id: str, expires_at: str) -> SourceRecord:
        """登记来源失效时间；到期后由 expire_due 统一标记，不静默继续引用。"""
        if not expires_at or not expires_at.strip():
            raise SourceImportError("expires_at不能为空")
        record = self._load_record(source_id)
        record.expires_at = expires_at.strip()
        self._replace_record(record)
        return record

    def expire_due(self, *, now: str = "") -> list[dict]:
        """把已到 expires_at 的来源标为 expired，并返回状态变化清单。"""
        current = now or datetime.datetime.now().isoformat(timespec="seconds")
        changed = []
        for item in self.load_index():
            expires_at = (item.get("expires_at") or "").strip()
            if not expires_at or item.get("status") not in ("ok", "partial"):
                continue
            if expires_at <= current:
                record = self._load_record(item["source_id"])
                record.status = "expired"
                record.withdrawn_at = current
                record.withdrawal_reason = f"来源于 {expires_at} 过期"
                record.status_message = record.withdrawal_reason
                self._replace_record(record)
                changed.append(record.meta())
        return changed

    def add_version(self, source_id: str, text: str, *, title: str = "",
                    published_date: str = "unknown") -> SourceRecord:
        """新增来源版本：旧版标 superseded，新版保留来源血缘和递增版本号。"""
        old = self._load_record(source_id)
        new = self._add(text=text, kind=old.kind, display=old.display,
                        original_address=old.original_address,
                        ext=old.content_format, encoding=old.encoding,
                        base_status="ok", base_message="", page_count=old.page_count)
        if new.status == "duplicate":
            raise SourceImportError("新版本正文与库内其他来源相同，不能作为独立版本")
        now = _now()
        new.title = (title or _title_of(text))[:200]
        new.published_date = published_date or "unknown"
        new.source_version = int(old.source_version or 1) + 1
        new.root_source_id = old.root_source_id or old.source_id
        new.source_job_id = old.source_job_id
        new.supersedes = old.source_id
        new.retrieved_at = now
        self._replace_record(new)
        old.status = "superseded"
        old.superseded_by = new.source_id
        old.status_message = f"已被新版本 {new.source_id} 替代"
        self._replace_record(old)
        return new

    def find_dependents(self, source_id: str) -> list[dict]:
        """扫描任务 JSON 产物，找引用该来源或其共享根来源的下游文件。"""
        if not source_id:
            return []
        jobs_root = (self.job_dir.parent.parent
                     if self.job_dir.name == "shared_sources"
                     else self.job_dir.parent)
        refs = []
        for path in jobs_root.rglob("*.json"):
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if source_id not in raw:
                continue
            refs.append({"path": path.relative_to(jobs_root).as_posix(),
                         "kind": path.stem})
        return sorted(refs, key=lambda item: item["path"])

    # ---- 读取 -----------------------------------------------------------
    def segments(self, source_id: str) -> list[dict]:
        """返回来源段落定位；PDF 记录的 page 字段用于生成页码定位。"""
        return list(self._load_record(source_id).segments)

    def full_text(self, source_id: str) -> str | None:
        """按 source_id 返回全文；失败/重复来源没有全文（返回 None）。"""
        for record in self.load_index():
            if record.get("source_id") == source_id:
                if not record.get("file_name"):
                    return None
                path = resolve_under(self.job_dir, record["file_name"])
                try:
                    return path.read_text(encoding="utf-8")
                except OSError:
                    return None
        return None

    def summary(self) -> dict:
        index = self.load_index()
        counts: dict[str, int] = {}
        usable = 0
        total_bytes = 0
        for record in index:
            counts[record["status"]] = counts.get(record["status"], 0) + 1
            if record["status"] in ("ok", "partial"):
                usable += 1
                total_bytes += int(record.get("byte_size") or 0)
        return {"schema_version": 1, "total": len(index), "usable": usable,
                "statuses": counts, "stored_bytes": total_bytes,
                "sources": index}


def import_texts_and_files(job_dir: Path, texts: tuple[str, ...],
                           files: tuple[str, ...]) -> SourceStore:
    """执行整批本地导入（S2-01 分类、S2-05 登记、S2-06 去重与定位）。

    抛出 SourceImportError（带全部条目说明）当且仅当一个可用来源都没有；
    部分失败会登记进索引并由调用方展示，不静默吞掉。
    """
    if len(texts) + len(files) > MAX_SOURCES:
        raise SourceImportError(
            f"资料超过单任务 {MAX_SOURCES} 个来源的上限，请缩小导入范围")
    store = SourceStore(job_dir)
    for index, text in enumerate(texts, start=1):
        store.add_paste(text, display_index=index)
        _check_total(store)
    for path in files:
        store.add_file(path)
        _check_total(store)
    summary = store.summary()
    if summary["usable"] == 0:
        notes = []
        for record in summary["sources"]:
            label = record.get("display") or record.get("source_id")
            if record["status"] == "duplicate":
                notes.append(f"{label}：{record.get('status_message')}")
            else:
                notes.append(f"{label}：{record.get('status_message') or record['status']}")
        detail = "；".join(notes) if notes else "没有可用资料"
        raise SourceImportError(f"导入后没有可用资料：{detail}")
    return store


def check_total_limit(store: SourceStore) -> None:
    """累计正文超过10MB时明确拒绝，不静默继续导入。"""
    if store.summary()["stored_bytes"] > MAX_TOTAL_BYTES:
        raise SourceImportError(
            f"任务累计正文超过 {MAX_TOTAL_BYTES // (1024 * 1024)}MB 上限，请缩小导入范围")


def _check_total(store: SourceStore) -> None:
    check_total_limit(store)
