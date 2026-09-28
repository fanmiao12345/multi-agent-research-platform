# -*- coding: utf-8 -*-
"""P1 导出套件（EX-02/03/04/05、TP-01/02/05/06、UX-01/02，设计见
docs/EXPORT_AND_REPORT_TEMPLATE_PLAN.md）。

只读派生层：只读任务目录内已落盘的产物与账目，不改执行语义、不产生新等级判定。
R8 约束（2026-09-22 用户澄清，优先级最高）：参考来源与任何章节都保持纯文本，
**导出内容一律不含链接或 URL**；可点入口只在本机界面（SR-01～06，见 workbench）。
"""
from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.harness.storage.paths import resolve_under

_LEVELS = {"accepted": "成品", "draft": "草稿", "unable": "无法完成",
           "failed": "失败"}
# 单次模型调用受 timeout 约束不会产生合法的超长间隔；正则只服务验收自检。
_URL_RE = re.compile(r"https?://|ftp://|file://|\]\(\s*http|<a[\s>]", re.IGNORECASE)
_FILENAME_BAD = re.compile(r"[/\\:*?\"<>|\x00-\x1f]")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_CITE = re.compile(r"\[E-(\d{3})\]")


def assert_no_urls(text: str) -> None:
    """验收⑧的硬断言：导出产物内不得出现链接或 URL（R8）。"""
    if _URL_RE.search(text):
        raise AssertionError(f"导出内容含链接或 URL：{_URL_RE.search(text).group()!r}")


@dataclass
class Block:
    style: str          # title/conclusion/meta/note/heading/para/list/source/limit
    text: str
    rows: list = field(default_factory=list)   # meta 的 key: value 行


@dataclass
class ExportDoc:
    blocks: list
    meta: dict


# ---- 内容组装（TP-01/02/06） ---------------------------------------------

def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_artifact(job_dir: Path, artifact_id: str):
    index = _load(job_dir / "artifacts.json") or {}
    for record in index.get("artifacts", []):
        if record.get("artifact_id") == artifact_id:
            try:
                return record, resolve_under(
                    job_dir, record["file_name"]).read_text(encoding="utf-8")
            except Exception:
                return record, None
    return None, None


def _latest_artifact_id(job_dir: Path):
    index = _load(job_dir / "artifacts.json") or {}
    prose = [a for a in index.get("artifacts", [])
             if a.get("kind") in ("report", "analysis", "collection")]
    if not prose:
        return None
    return sorted(prose, key=lambda a: a.get("version") or 0)[-1]["artifact_id"]


def _hard_requirement_failures(body: str, request: dict) -> list:
    """TP-06：导出前复验硬约束；未通过项显式标注，不允许伪装成干净成品。"""
    failures = []
    headings = {m.group(2).strip() for line in body.splitlines()
                if (m := _MD_HEADING.match(line.strip()))}
    for name in request.get("required_sections") or ():
        if not any(name in h for h in headings):
            failures.append(f"缺少必需章节「{name}」")
    for claim in request.get("forbidden_claims") or ():
        if str(claim) and str(claim) in body:
            failures.append(f"出现禁用表述「{claim}」")
    for fact in request.get("key_facts") or ():
        if str(fact) and str(fact) not in body:
            failures.append(f"缺少关键事实「{fact}」")
    return failures


def build_export_document(job_dir: Path, artifact_id: str | None = None,
                          *, appendix: str = "sources") -> ExportDoc:
    job_dir = Path(job_dir)
    artifact_id = artifact_id or _latest_artifact_id(job_dir)
    if not artifact_id:
        raise FileNotFoundError("任务没有可导出的交付产物")
    record, body = _read_artifact(job_dir, artifact_id)
    if record is None:
        raise FileNotFoundError(f"产物不存在：{artifact_id}")
    if body is None:
        raise ValueError(f"产物文件不可读：{artifact_id}")
    body = body.strip()
    job = _load(job_dir / "job.json") or {}
    request = _load(job_dir / "request.json") or {}
    ledger = _load(job_dir / "ledger.json") or {}
    pipeline = job.get("pipeline") or {}
    sources_index = (_load(job_dir / "sources.json") or {}).get("sources", [])
    evidence = (_load(job_dir / "evidence.json") or {}).get("items", [])
    evidence_map = {e.get("evidence_id"): e for e in evidence}
    source_map = {s.get("source_id"): s for s in sources_index}

    # 标题与一句话结论（原样取自正文，不生成新内容）
    title, conclusion = "", ""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        m = _MD_HEADING.match(line.strip())
        if m and not title:
            title = m.group(2).strip()
            continue
        if title and line.strip():
            conclusion = re.sub(r"\[E-\d{3}\]|〔.*?〕", "", line.strip()).strip()
            conclusion = conclusion.lstrip("> ").strip()[:80]
            break
    title = title or (request.get("task") or job_dir.name)[:40]

    # 元信息（TP-02）
    level = pipeline.get("draft_level") or "draft"
    version = record.get("version") or 0
    failure_items = _hard_requirement_failures(body, request)
    try:
        generated = datetime.fromtimestamp(
            resolve_under(job_dir, record["file_name"]).stat().st_mtime)
    except Exception:
        generated = datetime.now()
    unresolved = pipeline.get("unresolved_citations")
    usable_sources = sum(1 for s in sources_index if s.get("status") in ("ok", "partial"))
    meta_rows = [
        ("任务目标", request.get("task") or ""),
        ("交付等级", f"{_LEVELS.get(level, level)}（{level}）"),
        ("版本", f"v{version}" + (f"（改稿自 {job['revises_job']}）"
                                  if job.get("revises_job") else "")),
        ("生成时间", generated.strftime("%Y-%m-%d %H:%M")),
        ("执行方式", f"{request.get('flow') or 'research'} / "
                    f"{request.get('orchestration') or 'fixed'} · 模型 {job.get('model') or '—'}"),
        ("引用", f"{pipeline.get('total_citations', 0)} 条"
                f"（未解析 {unresolved if unresolved is not None else '—'}）"),
        ("来源", f"可用 {usable_sources} / 总 {len(sources_index)}"),
        ("用量", f"{ledger.get('call_count', '—')} 次调用 · "
                f"${ledger.get('known_estimated_cost_usd') or 0:.3f} · "
                f"{ledger.get('elapsed_seconds') or '—'}s"),
    ]
    blocks: list = [Block("title", title)]
    if conclusion:
        blocks.append(Block("conclusion", f"一句话结论：{conclusion}"))
    blocks.append(Block("meta", "交付信息", rows=meta_rows))
    if failure_items:
        blocks.append(Block("note", "草稿：未通过项 " + "；".join(failure_items)
                            + "（导出前硬约束复验；不视为验收成功）"))
    blocks.append(Block("body_heading", "正文"))
    blocks.append(Block("para", body))

    # 局限与未决问题（TP-01：骨架必备；正文已有该章节则不重复）
    if "局限" not in body:
        limit_rows = []
        if unresolved:
            limit_rows.append(("未解析引用", f"{unresolved} 条（结论暂缺依据）"))
        unusable = len(sources_index) - usable_sources
        if unusable:
            limit_rows.append(("不可用来源", f"{unusable} 份（读取失败/重复/撤回）"))
        if pipeline.get("message"):
            limit_rows.append(("链内结论", pipeline["message"]))
        if pipeline.get("termination_reason") not in (None, "success"):
            limit_rows.append(("终止原因", str(pipeline["termination_reason"])))
        if not limit_rows:
            limit_rows.append(("说明", "正文未声明局限；以链内审校结论为准"))
        blocks.append(Block("limit", "局限与未决问题", rows=limit_rows))

    # 参考来源（SR-04/R8：纯文本——标题、定位、时间；不写 URL）
    cited_ids = list(dict.fromkeys(_CITE.findall(body)))
    source_lines = []
    for n, eid in enumerate(cited_ids, 1):
        ev = evidence_map.get(f"E-{eid}") or {}
        src = source_map.get(ev.get("source_id") or "", {})
        locator = ev.get("locator") or {}
        where = (f"第 {locator['paragraph']} 段" if locator.get("paragraph")
                 else (f"第 {locator.get('page')} 页" if locator.get("page") else ""))
        label = src.get("title") or src.get("display") or src.get("source_id") or "未知来源"
        status = "" if src.get("status") == "ok" else f" · 状态 {src.get('status', '—')}"
        source_lines.append(
            f"{n}. {label}" + (f" · {where}" if where else "")
            + (f" · 获取 {str(src.get('captured_at', ''))[:10]}" if src.get("captured_at") else "")
            + status)
    if not source_lines:
        source_lines.append("（正文无 [E-xxx] 引用标注）")
    blocks.append(Block("source", "参考来源", rows=[(None, s) for s in source_lines]))

    if appendix == "sources":
        rows = []
        for s in sources_index:
            label = s.get("title") or s.get("display") or s.get("source_id")
            kind = {"url": "网页", "file": "文件", "paste": "粘贴"}.get(
                s.get("kind"), s.get("kind") or "—")
            rows.append((None, f"- {label}（{kind} · {s.get('status', '—')}"
                        + (f" · 获取 {str(s.get('captured_at', ''))[:10]}"
                           if s.get("captured_at") else "") + "）"))
        blocks.append(Block("limit", "附录 B：来源清单", rows=rows))
    return ExportDoc(blocks=blocks, meta={"level": level, "version": version,
                                          "artifact_id": artifact_id})


# ---- EX-05：文件名 --------------------------------------------------------

def export_filename(task: str, level: str, version: int, when: str, ext: str,
                    fallback_id: str = "") -> tuple[str, str]:
    """返回 (ASCII 回退名, RFC 5987 名)；中文名不乱码、非法字符净化（EX-05）。"""
    digits = re.sub(r"\D", "", when or "")
    stamp = (f"{digits[:8]}-{digits[8:12]}" if len(digits) >= 12
             else datetime.now().strftime("%Y%m%d-%H%M"))
    short = _FILENAME_BAD.sub("_", (task or "").strip())[:40].strip("_ ") or ""
    base = f"{short}_{level}_v{version}_{stamp}.{ext}"
    if base.isascii():
        return base, f"filename*=UTF-8''{urllib.parse.quote(base)}"
    job8 = (fallback_id or "job").replace("job_", "")[:8]
    fallback = f"job_{job8}_{level}_v{version}_{stamp}.{ext}"
    return fallback, f"filename*=UTF-8''{urllib.parse.quote(base)}"


def content_disposition(task: str, level: str, version: int, when: str, ext: str,
                        fallback_id: str = "") -> str:
    ascii_name, rfc5987 = export_filename(task, level, version, when, ext, fallback_id)
    return (f'attachment; filename="{ascii_name}"; {rfc5987}'
            if ascii_name != urllib.parse.unquote(rfc5987.split("''", 1)[-1])
            else f'attachment; filename="{ascii_name}"')


# ---- EX-02：Markdown ------------------------------------------------------

def render_md(doc: ExportDoc) -> bytes:
    out = []
    for b in doc.blocks:
        if b.style == "title":
            out.append(f"# {b.text}")
        elif b.style == "conclusion":
            out.append(f"> {b.text}")
        elif b.style == "meta":
            out.append("## 交付信息")
            out.extend(f"- {k}：{v}" for k, v in b.rows)
        elif b.style == "note":
            out.append(f"> ⚠ {b.text}")
        elif b.style == "body_heading":
            out.append("## 正文")
        elif b.style == "limit" or b.style == "source":
            out.append(f"## {b.text}")
            out.extend(v if v.startswith("- ") else (v if v[0].isdigit() else f"- {v}")
                       for _, v in b.rows)
        elif b.style == "para":
            out.append(b.text)
    text = "\n\n".join(out) + "\n"
    assert_no_urls(text)
    return text.encode("utf-8")


# ---- EX-03：纯文本（BOM + 中文标题层级 + 软换行） --------------------------

_CN_NUM = "一二三四五六七八九十"


def _wrap(line: str, width: int = 100) -> list:
    return [line[i:i + width] for i in range(0, len(line), width)] or [""]


def render_txt(doc: ExportDoc) -> bytes:
    h1 = 0
    out: list = []
    for b in doc.blocks:
        if b.style == "title":
            out += _wrap(b.text)
        elif b.style == "conclusion":
            out += _wrap(f"【{b.text}】")
        elif b.style == "meta":
            out.append("【交付信息】")
            for k, v in b.rows:
                out.extend(_wrap(f"· {k}：{v}"))
        elif b.style == "note":
            out += _wrap(f"【⚠ {b.text}】")
        elif b.style == "body_heading":
            h1 = 0
        elif b.style in ("limit", "source"):
            h1 += 1
            out.append(f"{_CN_NUM[min(h1 - 1, 9)]}、{b.text}")
            for _, v in b.rows:
                out.extend(_wrap("· " + v.lstrip("- ")))
        elif b.style == "para":
            for line in b.text.splitlines():
                stripped = line.strip()
                if _MD_HEADING.match(stripped):
                    out.append("【" + _MD_HEADING.match(stripped).group(2) + "】")
                elif stripped.startswith("- "):
                    out.extend(_wrap("· " + stripped[2:]))
                else:
                    out.extend(_wrap(line))
    text = "\n".join(out)
    assert_no_urls(text)
    return b"\xef\xbb\xbf" + text.encode("utf-8")


# ---- EX-04：docx 最小 OOXML（零依赖，路线 A） ------------------------------

def _xml(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _docx_paragraph(text: str, *, size: int, bold: bool = False) -> str:
    b = "<w:b/><w:bCs/>" if bold else ""
    rpr = (f'<w:rPr><w:rFonts w:ascii="Calibri" w:eAsia="等线" w:hAnsi="Calibri"/>'
           f"{b}<w:sz w:val=\"{size}\"/><w:szCs w:val=\"{size}\"/></w:rPr>")
    return (f'<w:p><w:pPr><w:spacing w:line="360" w:lineRule="auto"/></w:pPr>'
            f'<w:r>{rpr}<w:t xml:space="preserve">{_xml(text)}</w:t></w:r></w:p>')


def render_docx(doc: ExportDoc) -> bytes:
    """最小 OOXML：标题 16pt/小标题 14pt/正文 11pt，1.5 行距，页边距 2.54cm（TP-05）。
    不含超链接关系——R8：导出内不含链接或 URL。"""
    parts = []
    for b in doc.blocks:
        if b.style == "title":
            parts.append(_docx_paragraph(b.text, size=32, bold=True))
        elif b.style in ("conclusion", "note"):
            parts.append(_docx_paragraph(b.text, size=20, bold=True))
        elif b.style == "meta":
            parts.append(_docx_paragraph("交付信息", size=28, bold=True))
            parts.extend(_docx_paragraph(f"{k}：{v}", size=22) for k, v in b.rows)
        elif b.style == "body_heading":
            parts.append(_docx_paragraph("正文", size=28, bold=True))
        elif b.style in ("limit", "source"):
            parts.append(_docx_paragraph(b.text, size=28, bold=True))
            parts.extend(_docx_paragraph(v.lstrip("- "), size=22) for _, v in b.rows)
        else:
            for line in b.text.splitlines() or [""]:
                m = _MD_HEADING.match(line.strip())
                if m:
                    parts.append(_docx_paragraph(m.group(2), size=28, bold=True))
                else:
                    parts.append(_docx_paragraph(
                        ("• " + line.lstrip("- ")) if line.lstrip().startswith("- ")
                        else line, size=22))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>' + "".join(parts) +
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
        '</w:body></w:document>')
    # 验收⑧：对用户可见文本（w:t）断言无 URL；XML 命名空间声明是结构必需不算内容
    assert_no_urls("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", document)))
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document)
    return buf.getvalue()
