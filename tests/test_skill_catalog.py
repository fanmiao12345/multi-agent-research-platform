# -*- coding: utf-8 -*-
"""技能目录：新增技能的解析、召回和权限边界检查。"""
from pathlib import Path

from src.harness.skills.recall import recall
from src.harness.skills.registry import SkillRegistry

ROOT=Path(__file__).resolve().parent.parent
NEW_SKILLS=("source-audit","literature-review","decision-brief",
            "data-check","risk-review","project-retrospective",
            "research-question","comparison-matrix","survey-design",
            "metric-definition","incident-triage","change-review",
            "release-checklist","meeting-actions","scenario-planning")


def test_new_skills_parse_and_load_without_issues():
    registry=SkillRegistry()
    names={skill.name for skill in registry.list()}
    assert set(NEW_SKILLS) <= names
    assert registry.diagnostics()["issues"] == {}


def test_new_skills_are_recalled_for_intended_scenarios():
    registry=SkillRegistry()
    cases={
        "来源审计 核查引用和事实支持": "source-audit",
        "做一份文献综述 研究现状": "literature-review",
        "比较方案并给出选型建议": "decision-brief",
        "核对数据 百分比和分母": "data-check",
        "安全风险与权限边界审查": "risk-review",
        "项目复盘 根因分析": "project-retrospective",
        "把目标拆成可回答的研究问题": "research-question",
        "比较矩阵 对比对象指标": "comparison-matrix",
        "设计问卷 抽样和调查偏差": "survey-design",
        "定义 KPI 分子分母和统计口径": "metric-definition",
        "线上故障分诊 报错排查": "incident-triage",
        "代码审查 diff 合并请求": "change-review",
        "发布检查 上线 版本冻结": "release-checklist",
        "会议纪要 行动项 决议": "meeting-actions",
        "情景规划 乐观 悲观 触发条件": "scenario-planning",
    }
    for query, expected in cases.items():
        candidates=recall(registry, query, top_k=5)
        assert candidates, query
        assert expected in {item["name"] for item in candidates}


def test_skill_permissions_are_not_expanded_by_catalog():
    registry=SkillRegistry()
    parent=frozenset({"calculator"})
    for name in NEW_SKILLS:
        skill=registry.get(name)
        effective=set(parent)
        if skill.allowed_tools:
            effective=effective.intersection(skill.allowed_tools)
        assert effective <= set(parent)


def test_catalog_documents_all_project_skills():
    catalog=(ROOT/"docs"/"SKILL_CATALOG.md").read_text(encoding="utf-8")
    for skill in SkillRegistry().list():
        assert f"`{skill.name}`" in catalog