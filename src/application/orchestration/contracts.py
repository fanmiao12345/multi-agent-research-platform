# -*- coding: utf-8 -*-
"""
D1-04 统一返回契约：交付等级、执行状态与归一化规则。

两套词汇严格分开（总计划 3.3），不得混用：
- 交付等级（交付了什么）：accepted=成品 / draft=待完善草稿 / unable=无法完成 /
  failed=失败（链内阶段错误）。"能诚实拒绝"不等于"写作质量达标"。
- 执行状态（流程怎么结束）：completed=正常结束 / partial=有产出但目标未完全达成 /
  failed=执行错误 / cancelled=取消或预算停止。

归一化规则 normalize_outcome：任何入口（链、执行器、评测）的返回都收敛为同一形状；
未知等级/状态一律落 failed/partial 对应的保守侧并保留原值，不猜测、不静默升级。
旧任务读取兼容：缺少新字段的旧 job.json/记录按缺省值处理，不被覆盖。
"""
from __future__ import annotations

DELIVERY_LEVELS = ("accepted", "draft", "unable", "failed")
DELIVERY_LABELS = {"accepted": "成品", "draft": "待完善草稿",
                   "unable": "无法完成", "failed": "失败"}
EXECUTION_STATUSES = ("completed", "partial", "failed", "cancelled")

# 终止原因 → 执行状态（与 research._CHAIN_TO_STATUS 同一口径，集中在此供多入口复用）
TERMINATION_TO_STATUS = {
    "success": "completed",
    "incomplete": "partial",
    "unable": "partial",
    "budget_exceeded": "cancelled",
    "cancelled": "cancelled",
    "error": "failed",
}


def normalize_level(draft_level) -> str:
    """交付等级归一：未知/缺失值保守落 failed，保留输入便于排查。"""
    level = (draft_level or "").strip()
    return level if level in DELIVERY_LEVELS else "failed"


def normalize_status(termination_reason, draft_level=None) -> str:
    """执行状态归一：先按终止原因映射；无法交付的 failed 等级至少 partial。"""
    status = TERMINATION_TO_STATUS.get((termination_reason or "").strip(), "failed")
    if status == "completed" and normalize_level(draft_level) == "failed":
        return "partial"      # 流程结束但没有可交付等级，不能标"正常完成"
    return status


def unified_record(*, root_job_id: str, draft_level, termination_reason,
                   message: str = "", **extra) -> dict:
    """统一返回形状：任何执行入口的收尾都产出这一结构（extra 为入口特有字段）。"""
    level = normalize_level(draft_level)
    record = {
        "schema_version": 2,
        "root_job_id": root_job_id,
        "draft_level": level,
        "delivery_label": DELIVERY_LABELS[level],
        "termination_reason": (termination_reason or "").strip(),
        "status": normalize_status(termination_reason, level),
        "message": message,
    }
    record.update(extra)
    return record
