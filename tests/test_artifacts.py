# -*- coding: utf-8 -*-
"""测试：受控产物存储（S2-07 / B3-03）。"""
import pytest

from src.harness.run_store import write_json
from src.harness.storage.artifacts import (
    ArtifactConflictError, ArtifactNotFoundError, ArtifactStore)
from src.harness.storage.paths import PathBoundaryError


def test_save_read_list_versioning(tmp_path):
    store = ArtifactStore(tmp_path / "job")
    first = store.save("report", "# 报告 v1\n\n正文。\n", producer="writer")
    second = store.save("report", "# 报告 v2\n\n修订。\n", parent_version=1,
                        producer="writer")
    assert first["artifact_id"] == "report.v1" and second["artifact_id"] == "report.v2"
    assert second["parent_version"] == 1 and second["version"] == 2
    assert first["content_hash"] != second["content_hash"]
    assert [a["artifact_id"] for a in store.list()] == ["report.v1", "report.v2"]
    assert store.read("report.v1")["text"] == "# 报告 v1\n\n正文。\n"
    assert (tmp_path / "job" / "artifacts" / "report.v1.md").exists()
    assert (tmp_path / "job" / "artifacts.json").exists()


def test_save_never_overwrites_existing_version(tmp_path):
    store = ArtifactStore(tmp_path / "job")
    store.save("report", "旧内容")
    store.save("report", "新内容")   # 自动 v2，永不覆盖 v1
    assert store.read("report.v1")["text"] == "旧内容"
    assert store.read("report.v2")["text"] == "新内容"


def test_index_duplicate_id_is_rejected(tmp_path):
    store = ArtifactStore(tmp_path / "job")
    store.save("report", "正文")
    index = store._load_index()
    index.append(dict(index[0], artifact_id="report.v1"))  # 模拟索引被篡改
    write_json(store.index_path, {"schema_version": 1, "artifacts": index})
    with pytest.raises(ArtifactConflictError, match="重复产物"):
        store.save("outline", "x")


@pytest.mark.parametrize("kind", ["", "bad kind", "Report", "a/b", "报告"])
def test_invalid_kind_rejected(tmp_path, kind):
    store = ArtifactStore(tmp_path / "job")
    with pytest.raises(ValueError):
        store.save(kind, "x")
    with pytest.raises(ValueError):
        store.save("report", "x", ext="html")


def test_missing_artifact_and_hash_tamper_are_detected(tmp_path):
    store = ArtifactStore(tmp_path / "job")
    store.save("report", "正文内容")
    with pytest.raises(ArtifactNotFoundError):
        store.read("report.v9")
    with pytest.raises(ArtifactNotFoundError):
        store.read("nope")
    path = tmp_path / "job" / "artifacts" / "report.v1.md"
    path.write_text("被篡改的内容", encoding="utf-8")
    with pytest.raises(ArtifactConflictError, match="哈希不一致"):
        store.read("report.v1")


def test_read_relative_path_within_artifacts_and_index(tmp_path):
    store = ArtifactStore(tmp_path / "job")
    store.save("report", "# 报告\n\n可读正文。\n")
    result = store.read_relative("artifacts/report.v1.md")
    assert result["text"].startswith("# 报告")
    with pytest.raises(ArtifactNotFoundError):
        store.read_relative("sources/x.txt")      # 不在 artifacts/
    with pytest.raises(PathBoundaryError):
        store.read_relative("../sources.json")     # 越界
    with pytest.raises(PathBoundaryError):
        store.read_relative(str(tmp_path / "job" / "artifacts" / "report.v1.md"))
    with pytest.raises(PathBoundaryError):
        store.read_relative("artifacts/../report.v1.md")  # ../ 一律按越界拒绝


def test_orphan_file_inside_artifacts_is_refused(tmp_path):
    store = ArtifactStore(tmp_path / "job")
    store.save("report", "正文")
    orphan = tmp_path / "job" / "artifacts" / "orphan.md"
    orphan.write_text("未登记内容", encoding="utf-8")
    with pytest.raises(ArtifactConflictError, match="未在索引登记"):
        store.read_relative("artifacts/orphan.md")


def test_corrupt_index_is_not_silently_overwritten(tmp_path):
    store = ArtifactStore(tmp_path / "job")
    store.save("report", "正文")
    store.index_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ArtifactConflictError, match="无法解析"):
        store.save("report", "更多正文")
    with pytest.raises(ArtifactConflictError, match="无法解析"):
        store.list()
