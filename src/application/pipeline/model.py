# -*- coding: utf-8 -*-
"""
application/pipeline/model.py —— 阶段数据契约（S3-04/05/06）

数据对象（对应实用化计划 4.2 的表）：
- Evidence：可定位的原文证据（evidence_id、source_id、摘录、字符/段落定位、支持的事实与标注）；
- MaterialPack：按主题组织的事实、证据、重复项、冲突与待补问题；
- OutlineSection：章节标题、用途、必须覆盖的证据与标注要求；
- HardRequirements：任务硬约束（必需章节/禁语/关键事实），由调用方显式给出并在链内复验；
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


MAX_HARD_ITEMS = 40
MAX_HARD_ITEM_CHARS = 200


@dataclass(frozen=True)
class HardRequirements:
    """任务硬约束（S6-05 对齐）：由调用方显式给出，链内程序层逐条复验。

    - required_sections：正文标题必须出现的章节名（逐字，程序层判 error）；
    - forbidden_claims：正文中不得出现的表述（S8-C：程序层只提示字面疑似命中
      （warn），是否构成语义违规由人工/独立评测判定，不因疑似命中阻塞验收）；
    - key_facts：必须覆盖的关键事实（尽量保留原文措辞；未覆盖记 warn 不阻塞，
      语义覆盖由人工/独立评测判定）。

    与"模型自己生成的提纲"不同：这些要求来自任务本身（工作台表单、评测数据集
    标注），链必须服从任务要求，不能用自造结构替代。
    """
    required_sections: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    key_facts: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("required_sections", "forbidden_claims", "key_facts"):
            value = getattr(self, name)
            if isinstance(value, (list, tuple)):
                value = tuple(value)
            elif isinstance(value, str):
                value = (value,)
            else:
                raise ValueError(f"{name} 必须为字符串序列")
            cleaned: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    raise ValueError(f"{name} 每一项必须是文本")
                item = item.strip()
                if not item:
                    continue
                if len(item) > MAX_HARD_ITEM_CHARS:
                    raise ValueError(f"{name} 单项超过 {MAX_HARD_ITEM_CHARS} 字上限")
                if item not in cleaned:
                    cleaned.append(item)
            if len(cleaned) > MAX_HARD_ITEMS:
                raise ValueError(f"{name} 超过 {MAX_HARD_ITEMS} 项上限")
            object.__setattr__(self, name, tuple(cleaned))

    @property
    def is_empty(self) -> bool:
        return not (self.required_sections or self.forbidden_claims or self.key_facts)

    def as_dict(self) -> dict:
        return {"required_sections": list(self.required_sections),
                "forbidden_claims": list(self.forbidden_claims),
                "key_facts": list(self.key_facts)}

    def prompt_block(self) -> str:
        """注入提纲/初稿/审校提示词的硬要求块（无要求时返回空串）。"""
        if self.is_empty:
            return ""
        lines = ["任务硬性要求（程序层会逐条复验，违反即判为阻塞问题）："]
        if self.required_sections:
            lines.append("- 必须出现的章节（正文标题请逐字使用这些名称，不要改写或省略）："
                         + "；".join(self.required_sections))
        if self.forbidden_claims:
            lines.append("- 禁止出现的表述（出现即判为阻塞问题）："
                         + "；".join(self.forbidden_claims))
        if self.key_facts:
            lines.append("- 必须覆盖的关键事实（尽量保留原文措辞）："
                         + "；".join(self.key_facts))
        return "\n".join(lines)


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
    draft_level: str = "failed"      # accepted | draft | failed | unable（S8：证据完全无法支撑任务时主动声明）
    termination_reason: str = ""     # success | incomplete | unable | budget_exceeded | error
    final_artifact_id: str = ""
    stages: list[dict] = field(default_factory=list)
    issue_counts: dict = field(default_factory=dict)
    revised_rounds: int = 0
    total_citations: int = 0
    unresolved_citations: int = 0
    final_text: str = ""
    message: str = ""
    # 任务硬约束的复验读数（S6-05 对齐：链内自检与独立评测同一口径可见）
    hard_checks: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"draft_level": self.draft_level,
                "termination_reason": self.termination_reason,
                "final_artifact_id": self.final_artifact_id,
                "stages": self.stages, "issue_counts": self.issue_counts,
                "revised_rounds": self.revised_rounds,
                "total_citations": self.total_citations,
                "unresolved_citations": self.unresolved_citations,
                "hard_checks": self.hard_checks,
                "message": self.message}
