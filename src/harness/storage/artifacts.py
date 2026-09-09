# -*- coding: utf-8 -*-
"""
harness/storage/artifacts.py —— 受控完整产物存储（S2-07）

- 模型与流程通过 artifact_id（如 report.v1）或相对路径访问完整产物；
- 每个 job 的产物保存在 job_dir/artifacts/ 下，artifacts.json 是唯一索引；
- 版本自动递增；同 kind 同版本禁止覆盖（旧版本始终可读）；
- 所有文件读写都先经过路径边界检查；索引条目与文件内容分开校验。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path

from src.harness.run_store import write_json
from src.harness.storage.paths import (PathBoundaryError, ensure_relative_name,
                                       resolve_under)
from src.harness.storage.sources import _atomic_write_text  # 复用原子写文本

KIND_PATTERN = r"^[a-z0-9_]{1,64}$"
_EXT_BY_KIND = {"md": "md", "txt": "txt", "json": "json"}
_ALLOWED_EXTS = {".md", ".txt", ".json"}


class ArtifactConflictError(ValueError):
    """同 kind+version 已存在：禁止覆盖历史产物。"""


class ArtifactNotFoundError(LookupError):
    """artifact_id 或相对路径在索引/受控目录中不存在。"""


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ArtifactStore:
    def __init__(self, job_dir: Path):
        self.job_dir = Path(job_dir)
        self.directory = resolve_under(job_dir, "artifacts")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_path = resolve_under(job_dir, "artifacts.json")

    # ---- 索引 -----------------------------------------------------------
    def _load_index(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        try:
            with self.index_path.open(encoding="utf-8") as f:
                data = json.load(f)
            records = data.get("artifacts", []) if isinstance(data, dict) else []
        except Exception:
            raise ArtifactConflictError("artifacts.json 无法解析，拒绝覆盖未知历史索引") from None
        ids = [r.get("artifact_id") for r in records]
        if len(set(ids)) != len(ids):
            raise ArtifactConflictError("artifacts.json 存在重复产物 id，拒绝继续写入")
        return records

    def list(self) -> list[dict]:
        return [self._public(r) for r in self._load_index()]

    @staticmethod
    def _public(record: dict) -> dict:
        return {k: record.get(k) for k in (
            "artifact_id", "kind", "version", "parent_version", "file_name",
            "content_hash", "byte_size", "producer", "created_at")}

    def _next_version(self, kind: str) -> int:
        versions = [int(r["version"]) for r in self._load_index()
                    if r.get("kind") == kind and isinstance(r.get("version"), int)]
        return (max(versions) + 1) if versions else 1

    # ---- 写入 -----------------------------------------------------------
    def save(self, kind: str, text: str, *, ext: str | None = None,
             parent_version: int | None = None, producer: str = "") -> dict:
        if not isinstance(kind, str) or not re.fullmatch(KIND_PATTERN, kind):
            raise ValueError(f"kind 只允许小写字母/数字/下划线（{kind!r}）")
        if not isinstance(text, str):
            raise ValueError("产物正文必须是文本")
        ext = ext or "md"
        if ext not in _EXT_BY_KIND:
            raise ValueError("ext 只支持 md/txt/json")
        if parent_version is not None and (isinstance(parent_version, bool)
                                           or not isinstance(parent_version, int)
                                           or parent_version < 1):
            raise ValueError("parent_version 必须为正整数")
        index = self._load_index()
        version = self._next_version(kind)
        artifact_id = f"{kind}.v{version}"
        if any(r.get("artifact_id") == artifact_id for r in index):
            raise ArtifactConflictError(f"产物已存在：{artifact_id}")
        file_name = f"{kind}.v{version}.{ext}"
        safe = ensure_relative_name(file_name)
        text_path = self.directory / safe
        _atomic_write_text(text_path, text)
        record = {
            "artifact_id": artifact_id, "kind": kind, "version": version,
            "parent_version": parent_version,
            "file_name": f"artifacts/{safe}",
            "content_hash": _sha256_text(text), "byte_size": len(text.encode("utf-8")),
            "producer": producer, "created_at": _now()}
        index.append(record)
        write_json(self.index_path, {"schema_version": 1, "artifacts": index})
        return self._public(record)

    # ---- 读取 -----------------------------------------------------------
    def read(self, artifact_id: str) -> dict:
        record = self.find(artifact_id)
        if record is None:
            raise ArtifactNotFoundError(f"产物不存在：{artifact_id}")
        path = resolve_under(self.job_dir, record["file_name"])
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            raise ArtifactNotFoundError(
                f"产物索引存在但文件缺失：{record['file_name']}") from None
        if _sha256_text(text) != record["content_hash"]:
            raise ArtifactConflictError(f"产物内容与索引哈希不一致：{artifact_id}")
        return self._public(record) | {"text": text}

    def find(self, artifact_id: str) -> dict | None:
        if not isinstance(artifact_id, str):
            return None
        for record in self._load_index():
            if record.get("artifact_id") == artifact_id:
                return record
        return None

    def read_relative(self, relative_path: str) -> dict:
        """通过受控相对路径读取（B4/B5 工具将提供给模型的访问方式）。

        只接受 artifacts/ 下的单段或深相对路径；绝对路径与任何 ../ 形式
        一律按路径边界拒绝，索引未登记的文件拒绝读取（索引是权威）。
        """
        if not isinstance(relative_path, str) or not relative_path:
            raise ArtifactNotFoundError("相对路径不能为空")
        raw = relative_path.replace("\\", "/")
        if Path(relative_path).is_absolute() or ".." in raw.split("/"):
            raise PathBoundaryError(f"产物路径必须是受控相对路径：{relative_path}")
        if not raw.startswith("artifacts/"):
            raise ArtifactNotFoundError(f"产物路径必须在 artifacts/ 内：{relative_path}")
        path = resolve_under(self.job_dir, raw)
        if path.suffix.lower() not in _ALLOWED_EXTS or not path.is_file():
            raise ArtifactNotFoundError(f"不是受支持的产物文件：{relative_path}")
        record = None
        for item in self._load_index():
            if item.get("file_name") == raw:
                record = item
                break
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            raise ArtifactNotFoundError(f"产物文件不可读：{relative_path}") from None
        if record is None:
            # 文件存在于受控目录但不在索引：拒绝访问，保持索引权威
            raise ArtifactConflictError(
                f"产物文件未在索引登记，拒绝读取：{relative_path}")
        if _sha256_text(text) != record["content_hash"]:
            raise ArtifactConflictError(
                f"产物内容与索引哈希不一致：{relative_path}")
        return self._public(record) | {"text": text}
