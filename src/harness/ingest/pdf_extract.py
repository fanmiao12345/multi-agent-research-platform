# -*- coding: utf-8 -*-
"""文本型 PDF 提取：限制页数与时间，保留页码范围；扫描件明确提示不支持 OCR。"""
from __future__ import annotations

import io
import time

MAX_PDF_PAGES = 200
MAX_PDF_SECONDS = 30.0


def extract_pdf(data: bytes) -> dict:
    """返回 {status, text, pages, message, page_count}。

    pages 中每项为 {page, start, end}，偏移相对提取后的纯文本，供来源段落定位。
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"status": "unsupported", "text": "", "pages": [],
                "page_count": 0,
                "message": "PDF 解析依赖 pypdf 未安装，无法读取文本型 PDF"}

    started = time.monotonic()
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception as e:  # noqa: BLE001 —— 第三方解析错误转明确来源状态
        return {"status": "read_failed", "text": "", "pages": [],
                "page_count": 0,
                "message": f"PDF 解析失败：{type(e).__name__}: {e}"}

    if getattr(reader, "is_encrypted", False):
        try:
            unlocked = reader.decrypt("")
        except Exception:  # noqa: BLE001
            unlocked = 0
        if not unlocked:
            return {"status": "unsupported", "text": "", "pages": [],
                    "page_count": len(reader.pages),
                    "message": "PDF 已加密，当前不支持需要密码的文档"}

    page_count = len(reader.pages)
    if page_count > MAX_PDF_PAGES:
        return {"status": "too_large", "text": "", "pages": [],
                "page_count": page_count,
                "message": f"PDF 共 {page_count} 页，超过 {MAX_PDF_PAGES} 页解析上限"}

    parts: list[str] = []
    pages: list[dict] = []
    warnings: list[str] = []
    cursor = 0
    for index, page in enumerate(reader.pages, start=1):
        if time.monotonic() - started > MAX_PDF_SECONDS:
            warnings.append(f"PDF 解析超过 {MAX_PDF_SECONDS:g} 秒，已完成 {index - 1}/{page_count} 页")
            break
        try:
            page_text = page.extract_text() or ""
        except Exception as e:  # noqa: BLE001 —— 单页失败保留其他页并注明
            page_text = ""
            warnings.append(f"第 {index} 页提取失败：{type(e).__name__}")
        page_text = "\n".join(line.rstrip() for line in page_text.splitlines()).strip()
        if parts:
            parts.append("\n\n")
            cursor += 2
        start = cursor
        parts.append(page_text)
        cursor += len(page_text)
        pages.append({"page": index, "start": start, "end": cursor})

    text = "".join(parts).strip()
    if not text:
        return {"status": "unsupported", "text": "", "pages": [],
                "page_count": page_count,
                "message": "PDF 没有可提取文本，可能是扫描件；当前不支持 OCR"}
    return {"status": "partial" if warnings else "ok", "text": text,
            "pages": pages, "page_count": page_count,
            "message": "；".join(warnings)}