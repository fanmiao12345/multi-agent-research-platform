# -*- coding: utf-8 -*-
"""
harness/skills —— Skill Registry / Recall / Router（DEV_PLAN D7-D8，步骤 49-52）

Skill = Markdown + Frontmatter：
    name / description / triggers / allowed_tools / version /（正文即 instructions）
    depends     可选：依赖的其他技能名（激活前自动解析，缺失/循环显式报错）
    references  可选：按需加载的参考文件名（三层渐进加载的第三层）
三层渐进式加载（progressive disclosure）：
    metadata     —— frontmatter（name/description/triggers），召回与路由只用这层
    fullcontent  —— 正文 instructions，仅命中的技能才注入上下文
    references   —— 大块参考资料，运行中确实需要时才 load_reference() 读取
内容默认放在项目根 skills/*.md（可用环境变量 SKILLS_DIR 覆盖）；
参考文件放在同名子目录：skills/<技能文件名去.md>/<引用名>.md。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SKILLS_DIR = Path(os.environ.get("SKILLS_DIR", PROJECT_ROOT / "skills"))

_KEY = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(.*)$")

# 渐进加载层级（收敛命名）
LEVEL_METADATA = "metadata"        # 只有 frontmatter 元数据
LEVEL_FULLCONTENT = "fullcontent"  # 元数据 + 正文指令


@dataclass
class Skill:
    name: str
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    version: str = ""
    depends: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
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
