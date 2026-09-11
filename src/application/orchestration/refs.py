# -*- coding: utf-8 -*-
"""
D1-03 引用契约：SourceRef / EvidenceRef / ArtifactRef 与结构化子结果。

目的：子智能体的交付不再是"一段裸文本"，而是可定位成果与原始资料的结构化结果——
摘要之外必须带"去哪个 job、哪个产物、哪条证据（引哪份来源）"的引用；
根任务的编排记录（orchestration.json）据此可追溯全部子成果。

边界（诚实声明）：本模块只定义并填充契约；最终报告的引用追溯原始资料（而不是
定位到子报告）在 D7-01 接通；当前子产出的摘要文本进入根任务时明确标注
"子智能体产出"，不冒充原始来源。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class SourceRef:
    """原始来源引用：可追溯到所属 job，并保留根共享来源与版本。"""
    job_id: str
    source_id: str
    root_source_id: str = ""
    source_version: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvidenceRef:
    """证据引用：evidence_id 在其所属 job 的 evidence.json 中可查，含原文摘录定位。"""
    job_id: str
    evidence_id: str
    source_id: str = ""
    quote_head: str = ""          # 原文摘录前 80 字，便于人不打开文件也能粗核

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ArtifactRef:
    """产物引用：kind 为产物类型（report/outline/material/review…），name 为文件名。"""
    job_id: str
    kind: str
    name: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StructuredSubResult:
    """结构化子结果：摘要 + 可定位引用，替代裸文本交接。"""
    child_job_id: str
    role: str
    draft_level: str = ""
    summary: str = ""                       # 子运行最终文本（明确标注为子产出，非原始来源）
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    artifact_refs: list[ArtifactRef] = field(default_factory=list)
    source_refs: list[SourceRef] = field(default_factory=list)
    truncation_note: str = ""               # 摘要被截断/省略时必须显式说明

    def to_dict(self) -> dict:
        return {
            "child_job_id": self.child_job_id, "role": self.role,
            "draft_level": self.draft_level,
            "summary": self.summary,
            "truncation_note": self.truncation_note,
            "evidence_refs": [r.to_dict() for r in self.evidence_refs],
            "artifact_refs": [r.to_dict() for r in self.artifact_refs],
            "source_refs": [r.to_dict() for r in self.source_refs],
        }


def collect_child_refs(workspace_root, child_job_id: str, *,
                       max_evidence: int = 20, summary_chars: int = 500) -> StructuredSubResult:
    """从子任务 job 目录收集结构化引用（只读；文件缺失时字段留空并注明）。"""
    base = workspace_root / "jobs" / child_job_id if workspace_root else None
    result = StructuredSubResult(child_job_id=child_job_id, role="")
    if base is None or not base.is_dir():
        result.truncation_note = "子任务目录不存在，无法收集引用"
        return result
    evidence_path = base / "evidence.json"
    if evidence_path.exists():
        try:
            import json
            items = json.loads(evidence_path.read_text(encoding="utf-8")).get("items", [])
            for item in items[:max_evidence]:
                result.evidence_refs.append(EvidenceRef(
                    job_id=child_job_id,
                    evidence_id=str(item.get("evidence_id", "")),
                    source_id=str(item.get("source_id", "")),
                    quote_head=str(item.get("quote", ""))[:80]))
            if len(items) > max_evidence:
                result.truncation_note = (f"证据引用已截断：共 {len(items)} 条，"
                                          f"仅列出前 {max_evidence} 条")
        except (ValueError, TypeError) as e:
            result.truncation_note = f"evidence.json 读取失败：{type(e).__name__}"
    artifacts_dir = base / "artifacts"
    if artifacts_dir.is_dir():
        for path in sorted(artifacts_dir.glob("*")):
            kind = path.name.split(".")[0]
            result.artifact_refs.append(ArtifactRef(job_id=child_job_id,
                                                    kind=kind, name=path.name))
    sources_path = base / "sources.json"
    if sources_path.exists():
        try:
            import json
            records = json.loads(sources_path.read_text(encoding="utf-8")).get("sources", [])
            result.source_refs = [SourceRef(
                job_id=child_job_id,
                source_id=str(r.get("source_id") or r.get("id", "")),
                root_source_id=str(r.get("root_source_id") or ""),
                source_version=int(r.get("source_version") or 1))
                for r in records]
        except (ValueError, TypeError):
            result.truncation_note = (result.truncation_note
                                      + "；sources.json 读取失败").lstrip("；")
    return result
