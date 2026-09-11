# -*- coding: utf-8 -*-
"""D3-04：文本型 PDF 的页文本、页码定位和失败分类。"""
from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import (DictionaryObject, NameObject, StreamObject)

from src.harness.ingest import pdf_extract
from src.harness.storage.sources import SourceStore


def _text_pdf(page_texts):
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        stream = StreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(
            f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_text_pdf_import_keeps_page_numbers(tmp_path):
    path = tmp_path / "sample.pdf"
    path.write_bytes(_text_pdf(["page one evidence", "page two evidence"]))
    store = SourceStore(tmp_path / "job")
    record = store.add_file(path)
    assert record.status == "ok" and record.page_count == 2
    assert "page one evidence" in store.full_text(record.source_id)
    segments = store.segments(record.source_id)
    by_text = {store.full_text(record.source_id)[s["start"]:s["end"]].strip(): s
               for s in segments}
    assert by_text["page one evidence"]["page"] == 1
    assert by_text["page two evidence"]["page"] == 2


def test_blank_pdf_reports_ocr_not_fake_text(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(_text_pdf([""]))
    record = SourceStore(tmp_path / "job").add_file(path)
    assert record.status == "unsupported"
    assert "OCR" in record.status_message


def test_pdf_page_limit_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_extract, "MAX_PDF_PAGES", 1)
    path = tmp_path / "many.pdf"
    path.write_bytes(_text_pdf(["one", "two"]))
    record = SourceStore(tmp_path / "job").add_file(path)
    assert record.status == "too_large"
    assert "页" in record.status_message