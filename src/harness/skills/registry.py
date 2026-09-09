# -*- coding: utf-8 -*-
"""
harness/skills/registry.py —— Skill Registry（步骤 50）

现读磁盘 skills/*.md（热插拔）；坏文件校验后跳过并记录问题，绝不崩溃。
提供：list / get / reload / diagnostics。
"""

from __future__ import annotations

from src.harness.skills.types import (SKILLS_DIR, Skill, parse_skill_md,
                                      validate_skill)


class SkillRegistry:
    def __init__(self, root=SKILLS_DIR):
        self.root = root
        self._skills: dict[str, Skill] = {}
        self._issues: dict[str, list[str]] = {}
        self.reload()

    def reload(self) -> None:
        """重新扫描目录（热插拔：增删改技能文件后调用即生效）。"""
        self._skills.clear()
        self._issues.clear()
        if not self.root.is_dir():
            return
        for path in sorted(self.root.glob("*.md")):
            if path.name.startswith("_"):
                continue  # 下划线开头 = 草稿/未启用
            try:
                meta = parse_skill_md(path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                self._issues[path.stem] = [f"读取失败：{e}"]
                continue
            issues = validate_skill(meta)
            if issues:
                self._issues[path.stem] = issues
                continue
            self._skills[meta["name"]] = Skill(
                name=meta["name"],
                description=meta.get("description", ""),
                triggers=[x.strip() for x in re_split(meta.get("triggers", ""))],
                allowed_tools=[x.strip() for x in re_split(meta.get("allowed_tools", ""))],
                version=meta.get("version", ""),
                instructions=meta["instructions"])
        self.last_scan = sorted(self._skills)

    def list(self) -> list[Skill]:
        return [self._skills[n] for n in sorted(self._skills)]

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def diagnostics(self) -> dict:
        return {"skills": self.last_scan, "issues": self._issues}


def re_split(raw: str) -> list[str]:
    import re
    return [x for x in re.split(r"[，,、\s]+", raw or "") if x]
