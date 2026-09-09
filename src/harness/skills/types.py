# -*- coding: utf-8 -*-
"""
harness/skills —— Skill Registry / Recall / Router（DEV_PLAN D7-D8，步骤 49-52）

Skill = Markdown + Frontmatter：
    name / description / triggers / allowed_tools / version /（正文即 instructions）
内容默认放在项目根 skills/*.md（可用环境变量 SKILLS_DIR 覆盖）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SKILLS_DIR = Path(os.environ.get("SKILLS_DIR", PROJECT_ROOT / "skills"))

_KEY = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(.*)$")


@dataclass
class Skill:
    name: str
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    version: str = ""
    instructions: str = ""


def parse_skill_md(text: str) -> dict:
    """解析技能 md：frontmatter + 正文(instructions)。"""
    meta: dict = {}
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            s = line.strip()
            if s == "---":
                break
            m = _KEY.match(s)
            if m:
                meta[m.group(1).lower()] = m.group(2).strip().strip("\"'")
    start = 0
    for i, line in enumerate(lines):
        if line.strip() == "---" and i > 0:
            start = i + 1
            break
    instructions = "\n".join(lines[start:]).strip()
    meta.setdefault("instructions", instructions)
    return meta


def validate_skill(meta: dict) -> list[str]:
    """Schema 校验（步骤 49 的完整性检查）：返回问题列表，空 = 合法。"""
    issues = []
    for field_name in ("name", "description"):
        if not meta.get(field_name, "").strip():
            issues.append(f"缺 frontmatter 字段：{field_name}")
    if not (meta.get("instructions") or "").strip():
        issues.append("技能正文（instructions）为空")
    return issues
