# -*- coding: utf-8 -*-
"""S4 子进程崩溃-恢复验收用桩大脑（独立模块便于 -c 子进程导入）。"""
import json
import os
import re

from src.llm.base import ChatResult


class S4Brain:
    """最小链大脑：mode=good 完整跑通；crash_at_outline 在提纲后 os._exit。"""
    model_name = "s4-stub"
    run_mode = "mock"
    provider = "stub"

    def __init__(self, mode="good"):
        self.mode = mode
        self.purposes = []
        self._drafts = 0

    def chat(self, messages, tools=None):
        system = next(m["content"] for m in messages if m["role"] == "system")
        user = next((m["content"] or "") for m in messages if m["role"] == "user")
        purpose = self._purpose(system)
        self.purposes.append(purpose)
        return ChatResult(content=self._reply(purpose, user))

    @staticmethod
    def _purpose(system):
        for marker, name in (("证据提取器", "evidence"), ("素材整理器", "material"),
                             ("提纲规划器", "outline"), ("报告写作者", "draft"),
                             ("审校员", "review")):
            if marker in system:
                return name
        return "unknown"

    def _reply(self, purpose, user):
        if purpose == "evidence":
            body = user.split("---- 来源全文开始 ----", 1)[-1].split(
                "---- 来源全文结束 ----", 1)[0]
            items = []
            for line in body.splitlines():
                if line.strip() and not line.strip().startswith("#"):
                    quote = line.strip()[:30]
                    items.append({"fact": f"要点：{quote}…", "tag": "F",
                                  "quote": quote})
            return json.dumps({"items": items[:2]}, ensure_ascii=False)
        ids = list(dict.fromkeys(re.findall(r"E-\d{3}", user)))
        if purpose == "material":
            return json.dumps({"topics": [{"name": "主题甲", "points": [
                {"evidence_id": i, "statement": f"陈述{i}"} for i in ids]}],
                "conflicts": [], "gaps": []}, ensure_ascii=False)
        if purpose == "outline":
            if self.mode == "crash_at_outline":
                # 模拟进程在提纲阶段被杀死：检查点尚未写入
                os._exit(9)  # noqa: PLR1722 —— 故意模拟崩溃
            return json.dumps({"title": "S4报告", "sections": [
                {"heading": "背景", "required_evidence": [ids[0]],
                 "require_fact_markers": True},
                {"heading": "结论", "required_evidence": [ids[1]]}]},
                ensure_ascii=False)
        if purpose == "draft":
            self._drafts += 1
            revising = "上一稿开始" in user
            lines = ["# S4报告", ""]
            if revising:
                # 改稿模式：明确产生实质变更（追加修订说明，保留引用与结构）
                lines.append("修订说明：本稿已按要求改写并保留可核查引用，"
                             "删除不再受支持的表述。\n")
            for match in re.finditer(
                    r"^([^：\n]+)：([^\n]*?)；必须覆盖证据 ([^；\n]+)(；需事实/推断标注)?",
                    user, re.MULTILINE):
                heading, purpose_text, required_raw, markers = (
                    match.group(1), match.group(2), match.group(3), match.group(4))
                lines.append(f"## {heading.strip()}")
                if purpose_text.strip():
                    lines.append(f"本节目的：{purpose_text.strip()}")
                for req in [x.strip() for x in required_raw.split(",") if x.strip()]:
                    mark = "〔事实〕" if markers else ""
                    lines.append(f"支持见 [{req}]{mark}。")
                if markers:
                    lines.append("另注〔推断〕边界。")
                lines.append("")
            return json.dumps({"report_markdown": "\n".join(lines)},
                              ensure_ascii=False)
        return json.dumps({"issues": [], "verdict": "accepted"})
