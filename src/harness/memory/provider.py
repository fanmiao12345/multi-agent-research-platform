# -*- coding: utf-8 -*-
"""
harness/memory/provider.py —— Memory Provider 抽象 + Prompt Cache 友好注入

Provider 抽象：记忆怎么"变成上下文块"是可替换的——
    LayeredMemoryProvider  三层记忆（默认）
    StaticProvider         固定文本（测试/演示用）
调用方（Runtime / 编排）只依赖 render() 一个方法。

Prompt Cache 前缀不变的关键：模型服务的 prompt cache 以请求前缀是否逐字节
一致为准。记忆块如果拼进 system 消息，每次检索结果不同 → system 前缀漂移
→ 缓存全 miss。因此这里统一把记忆块注入 **User Message**（本轮问题前面、
带 <<CONTEXT>> 分隔标记），system 前缀保持逐字节稳定，缓存前缀命中率不降。
"""

from __future__ import annotations

from src.harness.context.budget import estimate_tokens

# 注入 User Message 时的分隔标记：model_gateway 依赖它识别"已组装"的消息
MEMORY_MARKER = "<<CONTEXT>>"


class MemoryProvider:
    """记忆提供者接口：render(query) → 可注入的记忆文本块。"""

    name = "base"

    def render(self, query: str, top_k: int = 3) -> str:
        raise NotImplementedError

    def stats(self) -> dict:
        return {"name": self.name}


class StaticProvider(MemoryProvider):
    """固定文本块（测试/演示）。"""

    def __init__(self, text: str, name: str = "static"):
        self.text = text
        self.name = name

    def render(self, query: str, top_k: int = 3) -> str:
        return self.text


class LayeredMemoryProvider(MemoryProvider):
    """三层记忆 → 一个带层级标注的记忆块。"""

    def __init__(self, layers, name: str = "layered_memory"):
        self.layers = layers
        self.name = name

    def render(self, query: str, top_k: int = 3) -> str:
        grouped = self.layers.search_all(query, top_k=top_k)
        sections: list[str] = []
        for rec in grouped.get("semantic", []):
            sections.append(f"- [语义/{rec.id}] {rec.content}")
        for rec in grouped.get("episodic", []):
            sections.append(f"- [情节/{rec.id}] {rec.content}")
        for item in grouped.get("working", [])[-5:]:
            sections.append(f"- [工作/{item.get('kind', 'note')}] {item.get('content', '')}")
        if not sections:
            return ""
        return "相关记忆（跨层检索命中，可引用但需自行核实）：\n" + "\n".join(sections)

    def stats(self) -> dict:
        return {"name": self.name, **self.layers.snapshot()}


def build_user_message(question: str, memory_block: str) -> str:
    """把记忆块拼进本轮 User Message：记忆在前、问题在后，带 <<CONTEXT>> 标记。"""
    if not memory_block:
        return question
    return (f"{MEMORY_MARKER}\n[记忆]\n{memory_block.strip()}\n\n"
            f"[本轮问题]\n{question}")


def inject_into_user(messages: list[dict], memory_block: str,
                     question: str | None = None) -> list[dict]:
    """把记忆块并入 messages 里最后一条 user 消息（原对象不改，返回新列表）。

    - system 消息一律不动 → Prompt Cache 前缀逐字节稳定；
    - 找不到 user 消息且给了 question 时，追加一条带记忆的 user 消息；
    - 空记忆块原样返回，不产生空注入。
    """
    if not memory_block:
        return list(messages)
    out = [dict(m) for m in messages]
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user":
            content = out[i].get("content") or ""
            if MEMORY_MARKER in content:      # 已注入过的不重复叠
                return out
            out[i]["content"] = build_user_message(content, memory_block)
            return out
    if question is not None:
        out.append({"role": "user", "content": build_user_message(question, memory_block)})
    return out


def injection_report(messages_before: list[dict], messages_after: list[dict]) -> dict:
    """说明这次注入改变了什么（可解释性：注入前后必须可对照）。"""
    before_sys = [m.get("content", "") for m in messages_before if m.get("role") == "system"]
    after_sys = [m.get("content", "") for m in messages_after if m.get("role") == "system"]
    delta = sum(estimate_tokens(str(m.get("content") or "")) for m in messages_after) - \
        sum(estimate_tokens(str(m.get("content") or "")) for m in messages_before)
    return {"system_prefix_stable": before_sys == after_sys,
            "added_tokens_est": delta}
