# -*- coding: utf-8 -*-
"""
agents/profiles.py —— Agent Profile 注册表（DEV_PLAN G1 / 步骤 74）

Role 只声明：prompt / skills / tools / model_profile / permissions / output_schema。
Role 不创建独立 Runtime —— 统一 Harness（AgentRuntime + ToolRegistry + Context）
按 profile 组装一次运行。这也是 D-007「Multi-Agent 是 Policy」的载体。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentProfile:
    name: str
    title: str
    prompt: str
    tools: tuple = ()
    skills: tuple = ()
    model_profile: str = "default"
    permissions: frozenset = frozenset()
    output_schema: str = "自由文本"

    def describe(self) -> str:
        return (f"{self.name}（{self.title}） tools={list(self.tools)} "
                f"model={self.model_profile} 输出={self.output_schema}")


PROFILES: dict[str, AgentProfile] = {
    "researcher": AgentProfile(
        name="researcher", title="研究员",
        prompt="你是研究员：把主题查成带来源的事实清单，只采信工具真实返回，失败写待人工核实。",
        tools=("web_search", "fetch_page"), model_profile="default",
        permissions=frozenset(), output_schema="《原始资料》：逐条【事实】（来源）",
    ),
    "organizer": AgentProfile(
        name="organizer", title="整理师",
        prompt="你是整理师：把资料提炼成《素材包》（论点+证据+来源+缺口），不新增事实。",
        output_schema="《素材包》Markdown",
    ),
    "writer": AgentProfile(
        name="writer", title="撰稿人",
        prompt="你是撰稿人：基于素材包写成稿，事实有出处、文末列参考来源，不编造。",
        output_schema="成品文章 Markdown",
    ),
    "reviewer": AgentProfile(
        name="reviewer", title="审校",
        prompt="你是审校：对照素材四查（事实/来源/结构/表达），输出「通过」或编号问题清单。",
        output_schema="通过 / 问题清单",
    ),
    # D2-03：editor 与契约角色白名单（researcher/organizer/writer/editor/agent）对齐；
    # 审校类角色默认无工具（写作链内审校以素材包为界）。
    "editor": AgentProfile(
        name="editor", title="编辑",
        prompt="你是编辑：对照素材核查事实、来源与结构，输出「通过」或编号问题清单，不新增事实。",
        output_schema="通过 / 问题清单",
    ),
}


def get_profile(name: str) -> AgentProfile:
    if name not in PROFILES:
        raise KeyError(f"未知角色 {name}，可用：{sorted(PROFILES)}")
    return PROFILES[name]
