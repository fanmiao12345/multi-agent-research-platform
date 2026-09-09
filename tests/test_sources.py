# -*- coding: utf-8 -*-
"""测试：来源登记/导入分类/段落定位/去重（S2-01/S2-05/S2-06 本地部分 / B3-02）。"""
import pytest

from src.harness.storage.sources import (
    MAX_BYTES_PER_SOURCE, SourceImportError, SourceStore, detect_format,
    import_texts_and_files, split_segments)


def _text_source(tmp_path, name="a.txt", content="第一段。\n\n第二段。\n", encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(content.encode(encoding))
    return path


def test_paste_and_file_import_registers_full_text(tmp_path):
    store = SourceStore(tmp_path / "job")
    store.add_paste("仅依据提供资料，不得自行联网补充事实。", display_index=1)
    file = _text_source(tmp_path, "notes.md", "# 标题\n\n正文段落甲。\n\n正文段落乙。\n")
    store.add_file(file)
    summary = store.summary()
    assert summary["total"] == 2 and summary["usable"] == 2
    by_kind = {s["kind"]: s for s in summary["sources"]}
    assert set(by_kind) == {"paste", "file"}
    md = by_kind["file"]
    assert md["status"] == "ok" and md["content_format"] == "md"
    assert md["published_date"] == "unknown"
    assert md["content_hash"] and md["segment_count"] == 2
    # 全文可读回且与原文一致（哈希验证）
    full = store.full_text(md["source_id"])
    assert full == "# 标题\n\n正文段落甲。\n\n正文段落乙。\n"
    assert md["title"] == "标题"
    # 登记的文件相对路径不允许越界读取
    assert md["file_name"].startswith("sources/")
    assert store.full_text("src_nope") is None


def test_empty_and_binary_and_blocked_are_classified(tmp_path):
    store = SourceStore(tmp_path / "job")
    empty = _text_source(tmp_path, "empty.md", "   \n\n")
    store.add_file(empty)
    store.add_paste("   ", display_index=1)
    blocked = _text_source(tmp_path, "scan.pdf", "%PDF-1.4 fake")
    store.add_file(blocked)
    binary = tmp_path / "blob.dat"
    binary.write_bytes(b"\x00\x01\x02\x03" * 300)
    store.add_file(binary)
    summary = store.summary()
    statuses = summary["statuses"]
    assert statuses.get("empty") == 2 and statuses.get("unsupported") == 2
    assert summary["usable"] == 0
    messages = [s["status_message"] for s in summary["sources"]]
    assert any("PDF" in m for m in messages)
    assert any("二进制" in m for m in messages)
    assert any("为空" in m for m in messages)


def test_gbk_and_undecodable_bytes_partial(tmp_path):
    store = SourceStore(tmp_path / "job")
    gbk = _text_source(tmp_path, "gbk.txt", "中文资料内容。", encoding="gb18030")
    store.add_file(gbk)
    bad = tmp_path / "bad.txt"
    bad.write_bytes("正常部分".encode("utf-8") + b"\xff\xfe\xfd" + "尾部".encode("utf-8"))
    store.add_file(bad)
    summary = store.summary()
    by_status = {s["status"]: s for s in summary["sources"]}
    assert by_status["ok"]["encoding"] == "gb18030"
    assert by_status["partial"]["encoding"] == "utf-8-replace"
    assert "尾部" in store.full_text(by_status["partial"]["source_id"])


def test_decode_failure_of_binary_with_text_extension_is_partial_or_binary(tmp_path):
    store = SourceStore(tmp_path / "job")
    weird = tmp_path / "weird.txt"
    weird.write_bytes(b"\x00abc\x00\x00")
    store.add_file(weird)
    statuses = store.summary()["statuses"]
    # NUL 字节 → 二进制嗅探判定 unsupported，即使扩展名是 .txt
    assert statuses.get("unsupported") == 1


def test_duplicate_by_hash_and_by_normalized_body(tmp_path):
    job = tmp_path / "job"
    store = SourceStore(job)
    first = _text_source(tmp_path, "one.md", "# 标题\n\n内容相同。\n")
    store.add_file(first)
    copy = _text_source(tmp_path, "two.md", "# 标题\n\n内容相同。\n")
    store.add_file(copy)
    reprint = _text_source(tmp_path, "three.txt", "# 标题\n\n内容相同。\n\n")
    store.add_file(reprint)
    summary = store.summary()
    assert summary["usable"] == 1 and summary["statuses"]["duplicate"] == 2
    dupes = [s for s in summary["sources"] if s["status"] == "duplicate"]
    assert all(d["duplicate_of"] == first_name(first, summary) for d in dupes)
    assert all(store.full_text(d["source_id"]) is None for d in dupes)


def first_name(_, summary):
    return [s["source_id"] for s in summary["sources"] if s["display"] == "one.md"][0]


def test_segments_locate_original_text(tmp_path):
    text = "第一段。\n\n## 子标题\n\n第二段。\n\n第三段。\n"
    segments = split_segments(text)
    assert len(segments) == 3
    assert segments[0]["heading"] == "" and segments[0]["start"] == 0
    assert segments[1]["heading"] == "## 子标题"
    for seg in segments:
        assert text[seg["start"]:seg["end"]].strip()
        assert seg["start"] <= seg["end"]
    assert segments[2]["paragraph"] == 2
    # 段落在原文中的偏移可重新拼出正文
    rebuilt = "".join(text[s["start"]:s["end"]].strip("\n") + "\n" for s in segments)
    assert "子标题" not in rebuilt


def test_detect_format_for_md_and_txt():
    assert detect_format("普通一行。\n", "txt") == "txt"
    assert detect_format("# H\n\ntext\n") == "md"
    assert detect_format("text\n", "markdown") == "md"


def test_oversize_and_total_limits_are_explicit(tmp_path):
    store = SourceStore(tmp_path / "job")
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * (MAX_BYTES_PER_SOURCE + 1))
    store.add_file(big)
    statuses = store.summary()["statuses"]
    assert statuses.get("too_large") == 1
    assert "上限" in store.summary()["sources"][0]["status_message"]
    assert store.summary()["usable"] == 0


def test_import_texts_and_files_zero_usable_raises_with_messages(tmp_path):
    job = tmp_path / "job"
    with pytest.raises(SourceImportError) as exc:
        import_texts_and_files(job, ("  ",), ())
    assert "没有可用资料" in str(exc.value) and "为空" in str(exc.value)
    job2 = tmp_path / "job2"
    blocked = _text_source(tmp_path, "x.pdf", "fake")
    with pytest.raises(SourceImportError) as exc:
        import_texts_and_files(job2, (), (str(blocked),))
    assert "PDF" in str(exc.value)


def test_import_mixed_partial_failure_keeps_records_and_notes(tmp_path):
    job = tmp_path / "job"
    missing = tmp_path / "no-such-file.txt"
    ok = _text_source(tmp_path, "ok.txt", "可用正文。\n")
    store = import_texts_and_files(job, ("粘贴正文甲",), (str(missing), str(ok)))
    summary = store.summary()
    assert summary["usable"] == 2
    assert summary["statuses"].get("read_failed") == 1
    assert summary["total"] == 3
    assert any(s["status"] == "read_failed" for s in summary["sources"])


def test_more_than_max_sources_rejected_before_import(tmp_path):
    job = tmp_path / "job"
    with pytest.raises(SourceImportError, match="上限"):
        import_texts_and_files(job, tuple(f"t{i}" for i in range(21)), ())
    # 20 个以内可以导入
    store = import_texts_and_files(job, tuple(f"内容 {i}。" for i in range(20)), ())
    assert store.summary()["usable"] == 20


def test_index_corruption_is_not_silently_overwritten(tmp_path):
    store = SourceStore(tmp_path / "job")
    store.add_paste("正文。", display_index=1)
    store.index_path.write_text("{broken json", encoding="utf-8")
    with pytest.raises(SourceImportError, match="无法解析"):
        store.add_paste("更多正文。", display_index=2)


def test_cumulative_total_limit_is_enforced(monkeypatch, tmp_path):
    import src.harness.storage.sources as module
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 10_000)
    job = tmp_path / "job"
    with pytest.raises(SourceImportError, match="累计正文"):
        import_texts_and_files(job, ("x" * 7_000, "y" * 7_000), ())
    # 未超限的批次正常完成
    job2 = tmp_path / "job2"
    store = import_texts_and_files(job2, ("x" * 3_000, "y" * 3_000), ())
    assert store.summary()["usable"] == 2
