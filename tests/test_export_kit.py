# -*- coding: utf-8 -*-
"""P1 导出套件测试：EX-02/03/04/05/07/10、TP-01/02/05/06、UX-01/02 与验收⑧（导出内无 URL）。

设计依据：docs/EXPORT_AND_REPORT_TEMPLATE_PLAN.md（2026-09-22 定稿，R8 澄清：
导出文件与产物一律不含链接或 URL，参考来源章节保持纯文本）。
"""
import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from src.harness.storage.export_kit import (
    assert_no_urls,
    build_export_document,
    export_filename,
    render_docx,
    render_md,
    render_txt,
)

# ---- 合成任务目录（字段结构与真实落盘一致） -----------------------------

SOURCE = {
    "source_id": "src_ab12cd34", "kind": "url", "status": "ok",
    "title": "第 2 周周报", "display": "第 2 周周报.md",
    "original_address": "https://intranet.example/week2",
    "captured_at": "2026-09-22T17:00:00", "file_name": "sources/src_ab12cd34.md",
    "http_status": 200,
}
EVIDENCE = {
    "evidence_id": "E-001", "source_id": "src_ab12cd34", "tag": "F",
    "fact": "试点40人，满意率75%", "quote": "满意率75%",
    "locator": {"heading": "", "paragraph": 3, "start": 10, "end": 40},
}
REPORT_TEXT = """# 月度综述：试点满意率

试点 40 人，满意率 75% [E-001]〔事实〕。没有设置对照组，外推需谨慎。

## 数据概览

- 覆盖试点全部 40 人 [E-001]〔事实〕
"""

REQUEST = {"task": "整理两份周报写月度综述", "mode": "real", "delivery_kind": "report",
           "required_sections": ["数据概览"], "forbidden_claims": [], "key_facts": []}


def _seed_job(tmp_path: Path) -> str:
    job = tmp_path / "jobs" / ("job_" + "a" * 32)
    (job / "sources").mkdir(parents=True)
    (job / "artifacts").mkdir()
    (job / "sources" / "src_ab12cd34.md").write_text("试点40人，满意率75%", encoding="utf-8")
    (job / "artifacts" / "report.v2.md").write_text(REPORT_TEXT, encoding="utf-8")
    (job / "artifacts" / "report.v1.md").write_text(REPORT_TEXT, encoding="utf-8")
    (job / "artifacts.json").write_text(json.dumps({"artifacts": [
        {"artifact_id": "report.v1", "kind": "report", "version": 1,
         "file_name": "artifacts/report.v1.md"},
        {"artifact_id": "report.v2", "kind": "report", "version": 2,
         "file_name": "artifacts/report.v2.md"}]}, ensure_ascii=False), encoding="utf-8")
    (job / "request.json").write_text(json.dumps(REQUEST, ensure_ascii=False), encoding="utf-8")
    (job / "job.json").write_text(json.dumps({
        "root_job_id": job.name, "status": "completed", "mode": "real",
        "model": "stub-model", "revises_job": "",
        "pipeline": {"draft_level": "accepted", "termination_reason": "success",
                     "final_artifact_id": "report.v2", "delivery_kind": "report",
                     "revised_rounds": 1, "total_citations": 1, "unresolved_citations": 0,
                     "hard_checks": {}, "message": "双层审校通过"}}, ensure_ascii=False),
        encoding="utf-8")
    (job / "ledger.json").write_text(json.dumps({
        "call_count": 9, "known_estimated_cost_usd": 0.041, "elapsed_seconds": 142.5},
        ensure_ascii=False), encoding="utf-8")
    (job / "sources.json").write_text(json.dumps({"sources": [SOURCE]}, ensure_ascii=False),
                                      encoding="utf-8")
    (job / "evidence.json").write_text(json.dumps({"items": [EVIDENCE]}, ensure_ascii=False),
                                       encoding="utf-8")
    return job.name


# ---- EX-05：文件名 -------------------------------------------------------

def test_export_filename_readable_and_safe():
    ascii_name, rfc5987 = export_filename("月度综述：RAG 落地", "accepted", 2,
                                          "2026-09-22T17:30:00", "md",
                                          fallback_id="job_aabbccdd" + "0" * 24)
    # 中文名走 RFC 5987，ASCII 回退用 job 前缀
    assert ascii_name.startswith("job_aabbccdd")
    assert rfc5987.startswith("filename*=UTF-8''")
    assert "%EF%BC%9A" in rfc5987 or ":" not in rfc5987.split("''", 1)[1]
    ascii2, _ = export_filename("plain ascii task", "draft", 1,
                                "2026-09-22T17:30:00", "docx")
    assert ascii2.startswith("plain ascii task") and "_v1_" in ascii2 \
        and ascii2.endswith(".docx")
    # 非法字符被净化
    _, bad = export_filename('a/b\\c:d*e?f"g<h>i|j', "accepted", 1,
                             "2026-09-22T17:30:00", "txt", fallback_id="job_x")
    assert not re.search(r'[/\\:*?"<>|]', bad.split("''", 1)[-1])


# ---- TP-01/02/06 + UX-02：内容组装 --------------------------------------

def test_build_document_contains_skeleton_and_meta(tmp_path):
    job_id = _seed_job(tmp_path)
    doc = build_export_document(tmp_path / "jobs" / job_id, "report.v2")
    text = render_md(doc).decode("utf-8")
    # 元信息块：等级/版本/时间/方式/引用/来源/用量
    assert "交付等级" in text and "成品" in text
    assert "v2" in text and "2026-09-22" in text
    assert "stub-model" in text and "9 次调用" in text
    # 硬约束复验：必需章节"数据概览"在正文里存在 → 不标未通过
    assert "未通过" not in text
    # 参考来源：纯文本（标题+定位+时间），不带 URL
    assert "参考来源" in text and "第 2 周周报" in text and "第 3 段" in text
    # 验收⑧：任何格式导出不含链接或 URL
    assert_no_urls(text)


def test_build_document_flags_failed_hard_requirements(tmp_path):
    job_id = _seed_job(tmp_path)
    req = dict(REQUEST, required_sections=["不存在的章节"], forbidden_claims=["满意率75%"])
    (tmp_path / "jobs" / job_id / "request.json").write_text(
        json.dumps(req, ensure_ascii=False), encoding="utf-8")
    doc = build_export_document(tmp_path / "jobs" / job_id, "report.v2")
    text = render_md(doc).decode("utf-8")
    # TP-06：未通过显式标注，不允许把问题稿伪装成干净成品
    assert "未通过" in text and "不存在的章节" in text


def test_document_includes_limits_section_when_body_lacks_one(tmp_path):
    job_id = _seed_job(tmp_path)
    doc = build_export_document(tmp_path / "jobs" / job_id, "report.v1")
    text = render_md(doc).decode("utf-8")
    assert "局限" in text


# ---- EX-02/03：md 与 txt -------------------------------------------------

def test_render_md_utf8_no_bom(tmp_path):
    job_id = _seed_job(tmp_path)
    doc = build_export_document(tmp_path / "jobs" / job_id, "report.v2")
    raw = render_md(doc)
    assert isinstance(raw, bytes) and not raw.startswith(b"\xef\xbb\xbf")


def test_render_txt_bom_and_numbered_headings(tmp_path):
    job_id = _seed_job(tmp_path)
    doc = build_export_document(tmp_path / "jobs" / job_id, "report.v2")
    raw = render_txt(doc)
    assert raw.startswith(b"\xef\xbb\xbf")           # EX-03：txt 带 BOM
    text = raw.decode("utf-8-sig")
    assert "一、" in text or "（一）" in text          # 中文标题层级
    assert_no_urls(text)


# ---- EX-04：docx 最小 OOXML ----------------------------------------------

def test_render_docx_is_minimal_valid_ooxml(tmp_path):
    job_id = _seed_job(tmp_path)
    doc = build_export_document(tmp_path / "jobs" / job_id, "report.v2")
    raw = render_docx(doc)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = set(zf.namelist())
    assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= names
    document = zf.read("word/document.xml").decode("utf-8")
    assert "满意率" in document                       # 正文进入 document.xml
    assert "w:line=" in document                      # 1.5 行距（TP-05）
    # 验收⑧：docx 用户可见文本（w:t）不含链接或 URL；且无超链接关系件。
    # （XML 命名空间声明含 "http://" 属 OOXML 结构必需，不算内容链接。）
    assert not any(n.startswith("word/_rels/") for n in names)
    visible = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", document))
    assert_no_urls(visible)
    assert "Hyperlink" not in document


# ---- 验收⑧：URL 断言器自身 ------------------------------------------------

def test_assert_no_urls_catches_links():
    assert_no_urls("普通文本 [E-001] 引用没问题")
    for bad in ("看 https://x.com/a", "http://x.com", "[点这里](https://x.com)",
                "<a href='https://x.com'>x</a>", "ftp://files"):
        with pytest.raises(AssertionError):
            assert_no_urls(bad)


# ---- EX-10：导出端点（旧端点不回归） ---------------------------------------

def test_web_export_endpoint_three_formats(tmp_path, monkeypatch):
    import threading
    from http.client import HTTPConnection
    from src.interfaces.web.workbench import make_server
    monkeypatch.setattr("src.harness.models.factory.build_adapter",
                        lambda *a, **kw: object())
    ws = tmp_path / "ws"
    ws.mkdir()
    job_id = _seed_job(ws)
    server = make_server(workspaces=str(ws), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=15)
    try:
        for fmt, marker in (("md", b"# "), ("txt", b"\xef\xbb\xbf"), ("docx", b"PK")):
            conn.request("GET", f"/api/jobs/{job_id}/export?format={fmt}")
            resp = conn.getresponse()
            raw = resp.read()
            assert resp.status == 200, (fmt, raw[:120])
            assert raw.startswith(marker), fmt
            disposition = resp.getheader("Content-Disposition")
            assert disposition and "attachment" in disposition
            assert resp.getheader("X-Content-Type-Options") == "nosniff"
        # 任意版本导出（EX-07）
        conn.request("GET", f"/api/jobs/{job_id}/export?format=md&artifact_id=report.v1")
        resp = conn.getresponse()
        assert resp.status == 200 and b"v1" in resp.read()
        # 非法参数与不存在的产物
        conn.request("GET", f"/api/jobs/{job_id}/export?format=pdf")
        assert conn.getresponse().status == 400
        conn.request("GET", f"/api/jobs/{job_id}/export?format=md&artifact_id=report.v9")
        assert conn.getresponse().status == 404
        # 旧端点不回归（验收④）
        conn.request("GET", f"/api/jobs/{job_id}/artifacts/report.v2/download")
        assert conn.getresponse().status == 200
        conn.request("GET", f"/api/jobs/{job_id}/export.html")
        assert conn.getresponse().status == 200
    finally:
        conn.close()
        state = server.RequestHandlerClass.state
        state.stop_worker()
        server.shutdown()
        server.server_close()
