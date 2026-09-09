# -*- coding: utf-8 -*-
"""
roles.py —— 角色注册表（像 skills/ 一样热插拔的数据驱动）

多智能体的「角色」不再写死在代码里，而是存放在 roles/*.md：
每个文件 = frontmatter 元信息 + 正文（该角色的 system 人设手册）。

格式：
    ---
    name: researcher            # 角色英文名（命令/计划里引用它）
    title: 研究员 Agent          # 展示名
    use_tools: true             # 该角色是否带工具
    budget: research            # 轮数预算档位：research | default
    description: 一句话简介      # 给主智能体组队时看的
    ---
    正文即 system prompt，可随意增删改 —— 运行中改动无需重启（每次现读磁盘）。

新增一个角色 = 在 roles/ 放一个 .md；multi.py / master.py 自动可用。
"""

from __future__ import annotations

import glob
import os
import re

ROLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roles")

_KEY = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(.*)$")


def load_role(name: str) -> dict | None:
    """现读磁盘加载单个角色（热插拔：增删改无需重启）。"""
    path = os.path.join(ROLES_DIR, f"{name}.md")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    meta: dict = {}
    body: list[str] = []
    in_front = bool(lines and lines[0].strip() == "---")
    if in_front:
        for line in lines[1:]:
            s = line.strip()
            if s == "---":
                break
            m = _KEY.match(s)
            if m:
                meta[m.group(1).lower()] = m.group(2).strip().strip("\"'")
    else:
        body = lines
    if in_front:
        # frontmatter 之后剩余的都是人设正文
        start = lines.index("---", 1) + 1 if "---" in lines[1:] else len(lines)
        body = lines[start:]
    return {
        "name": name,
        "title": meta.get("title", name),
        "use_tools": meta.get("use_tools", "").lower() in ("true", "yes", "1"),
        "budget": meta.get("budget", "default"),
        "description": meta.get("description", ""),
        "system": "\n".join(body).strip(),
    }


def role_system(name: str) -> str:
    """取角色的 system 人设（不存在时抛错，方便尽早发现名字写错）。"""
    role = load_role(name)
    if role is None or not role["system"]:
        raise RuntimeError(f"角色注册表里没有「{name}」（roles/{name}.md 缺失或正文为空）。"
                           f"可用角色: {', '.join(list_roles()) or '（无）'}")
    return role["system"]


def list_roles() -> list[str]:
    """所有已注册角色名（每次现读目录，热插拔）。"""
    return sorted(
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(os.path.join(ROLES_DIR, "*.md"))
    )
