# -*- coding: utf-8 -*-
"""O-20 冲突判定口径：素材阶段提示词必须收窄"开放冲突"的登记条件。

T09r/T10/T11 三例人工复核一致判定：模型把模式/范围/口径差异当"开放冲突"登记，
并被正文"开放冲突"小节继承——与 Q3-01 unfounded_conflict 误报同族（生成侧）。
修复：提示词明确"同一事实点直接互斥"才算冲突，条件/口径/版本差异不算。
"""
from src.application.pipeline.prompts import build_material_messages


def test_material_prompt_defines_conflict_criteria():
    msgs = build_material_messages("目标", "证据块")
    system = msgs[0]["content"]
    # 正向口径：只有同一事实点的直接互斥断言才可登记
    assert "同一事实点" in system and "互斥" in system
    # 反向排除：条件/口径/版本/适用范围差异与互补信息不是冲突
    assert "不同条件" in system or "口径" in system
    # 保留既有禁令：冲突 status 只能 open（S3-05 无依据消解矛盾禁令不放松）
    assert "只能" in system and "open" in system


def test_material_prompt_keeps_quantity_limits():
    """收窄口径不得放松既有结构约束（数量上限/证据 id 白名单）。"""
    system = build_material_messages("目标", "证据块")[0]["content"]
    assert "conflicts≤5" in system
    assert "evidence_id 只能使用给定列表中的" in system
