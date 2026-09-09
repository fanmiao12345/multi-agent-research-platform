# -*- coding: utf-8 -*-
"""
application/pipeline/model.py —— 阶段数据契约（S3-04/05/06）

数据对象（对应实用化计划 4.2 的表）：
- Evidence：可定位的原文证据（evidence_id、source_id、摘录、字符/段落定位、支持的事实与标注）；
- MaterialPack：按主题组织的事实、证据、重复项、冲突与待补问题；
- OutlineSection：章节标题、用途、必须覆盖的证据与标注要求；
- ReviewIssues：双层审校输出（程序 + 模型），错误/警告分级。

约定：稳定 id 用于关联；120/400 字摘要只用于列表显示，不能替代完整材料。
"""
from __future__ import annotations

from dataclasses import dataclass, field


class StageError(RuntimeError):
    """链上某一阶段失败（携带可展示消息与阶段名）。"""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


class StageBudgetStop(StageError):
    """根预算停止：要保存"待完善草稿"语义，不能由普通兜底吞掉。"""

    def __init__(self, stage: str, message: str = "根任务预算已停止"):
        super().__init__(stage, message)


@dataclass
class EvidenceItem:
    evidence_id: str            # E-001…（稳定，正文引用与评审都用它）
    source_id: str              # 来自 SourceStore
    fact: str                   # 支持的事实/主张
    tag: str                    # F=事实 I=推断 U=未知
    quote: str = ""             # 原文连续片段（程序先校验存在与定位）
    start: int = -1             # 在来源全文中的字符偏移（程序计算）
    end: int = -1
    locator: dict = field(default_factory=dict)  # heading/paragraph（来自分段meta口径）
    note: str = ""              # 无法定位/解码等说明

    def as_dict(self) -> dict:
        return {"evidence_id": self.evidence_id, "source_id": self.source_id,
                "fact": self.fact, "tag": self.tag, "quote": self.quote,
                "start": self.start, "end": self.end, "locator": self.locator,
                "note": self.note}

    @staticmethod
    def validate_tag(tag: str) -> str:
        tag = (tag or "").strip().upper()
        if tag not in ("F", "I", "U"):
            raise StageError("evidence", f"不支持的事实标注：{tag!r}（只能 F/I/U）")
        return tag


@dataclass
class MaterialPack:
    topics: list[dict] = field(default_factory=list)   # [{name, points:[{evidence_id, statement}]}]
    conflicts: list[dict] = field(default_factory=list)  # [{statement, evidence_ids, status:"open"}]
    gaps: list[dict] = field(default_factory=list)      # [{question, missing}]
    duplicates: list[dict] = field(default_factory=list)  # 程序从来源登记生成

    def as_dict(self) -> dict:
        return {"topics": self.topics, "conflicts": self.conflicts,
                "gaps": self.gaps, "duplicates": self.duplicates}


@dataclass
class OutlineSection:
    heading: str
    purpose: str = ""
    required_evidence: list[str] = field(default_factory=list)  # 本节必须覆盖的证据
    require_fact_markers: bool = False   # 正文中该节区分事实/推断标注

    def as_dict(self) -> dict:
        return {"heading": self.heading, "purpose": self.purpose,
                "required_evidence": self.required_evidence,
                "require_fact_markers": self.require_fact_markers}


@dataclass
class ReviewIssue:
    severity: str    # error | warn | info
    code: str        # citation|coverage|section|support|conflict|style|format
    message: str

    def as_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message}

    @staticmethod
    def validate_severity(value: str) -> str:
        value = (value or "").strip().lower()
        if value not in ("error", "warn", "info"):
            raise StageError("review", f"不支持的问题等级：{value!r}")
        return value


@dataclass
class PipelineResult:
    """整条链的结果与等级（S3-11：执行结束≠业务成功）。"""
    draft_level: str = "failed"      # accepted | draft | failed
    termination_reason: str = ""     # success | incomplete | budget_exceeded | error
    final_artifact_id: str = ""
    stages: list[dict] = field(default_factory=list)
    issue_counts: dict = field(default_factory=dict)
    revised_rounds: int = 0
    total_citations: int = 0
    unresolved_citations: int = 0
    final_text: str = ""
    message: str = ""

    def as_dict(self) -> dict:
        return {"draft_level": self.draft_level,
                "termination_reason": self.termination_reason,
                "final_artifact_id": self.final_artifact_id,
                "stages": self.stages, "issue_counts": self.issue_counts,
                "revised_rounds": self.revised_rounds,
                "total_citations": self.total_citations,
                "unresolved_citations": self.unresolved_citations,
                "message": self.message}
