# -*- coding: utf-8 -*-
"""测试：受控路径边界（S2-08 本地部分 / B3-01）。"""
import subprocess
from pathlib import Path

import pytest

from src.harness.storage.paths import (
    PathBoundaryError, canonical, ensure_under, resolve_under, ensure_relative_name)


def test_canonical_is_absolute_and_normalized(tmp_path):
    c = canonical(tmp_path / "sub" / ".." / "file.txt")
    assert c == canonical(tmp_path / "file.txt")
    assert c.is_absolute()


def test_resolve_under_accepts_deep_relative(tmp_path):
    p = resolve_under(tmp_path, "a/b/c.txt")
    assert p == canonical(tmp_path / "a/b/c.txt")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("ok", encoding="utf-8")
    assert p.read_text(encoding="utf-8") == "ok"


@pytest.mark.parametrize("bad", [
    "../outside.txt", "a/../../outside.txt", "..", "a/..",
    str(Path.cwd() / "outside.txt"),  # 绝对路径越界
])
def test_resolve_under_rejects_escape(tmp_path, bad):
    with pytest.raises(PathBoundaryError):
        resolve_under(tmp_path, bad)


def test_ensure_under_rejects_root_itself_and_sibling(tmp_path):
    with pytest.raises(PathBoundaryError):
        ensure_under(tmp_path, tmp_path)
    sibling = tmp_path.parent / (tmp_path.name + "_other")
    with pytest.raises(PathBoundaryError):
        ensure_under(sibling, tmp_path)


def _make_junction(link: Path, target: Path):
    """Windows junction 不需要管理员权限；失败时返回 None。"""
    try:
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return False
    return proc.returncode == 0


def test_junction_inside_root_escaping_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    link = root / "sub" / "linked"
    if not _make_junction(link, outside):
        pytest.skip("当前环境无法创建 Windows junction")
    assert link.exists()
    with pytest.raises(PathBoundaryError):
        resolve_under(root, "sub/linked/secret.txt")
    # 通过 junction 本身读外部文件也不允许
    with pytest.raises(PathBoundaryError):
        ensure_under(canonical(link / "secret.txt"), root)
    # 尚不存在的尾部（如将要创建的文件）也不能穿过 junction 逃逸
    with pytest.raises(PathBoundaryError):
        resolve_under(root, "sub/linked/not-yet/new.txt")


def test_ensure_relative_name_whitelist(tmp_path):
    assert ensure_relative_name("report.v1.md") == "report.v1.md"
    for bad in ("", "a/b", "..", ".", "\\evil", "/evil", "a\\b"):
        with pytest.raises(PathBoundaryError):
            ensure_relative_name(bad)


def test_sources_dir_isolation_layout(tmp_path):
    """job 目录与 artifacts 目录互相越界访问都应被拒。"""
    job = tmp_path / "jobs" / "job_x"
    (job / "artifacts").mkdir(parents=True)
    with pytest.raises(PathBoundaryError):
        resolve_under(job / "artifacts", "../sources")
    with pytest.raises(PathBoundaryError):
        resolve_under(job, "artifacts/../../../outside")
