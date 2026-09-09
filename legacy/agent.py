# -*- coding: utf-8 -*-
"""
agent.py —— 智能体主循环（这是全项目最重要的一个文件）

所谓「智能体循环」（ReAct: Reason + Act -> Observe），本质上就是三步反复执行：

    1. 问大脑(LLM)：当前情况 + 工具说明书 -> 它说「我要调用工具 X(参数...)」
    2. 动手执行：主循环运行工具，拿到真实结果（而不是模型编造的结果）
    3. 把结果喂回去：再问大脑 -> 它看结果决定：继续调用工具，还是给出最终回答

直到大脑说出纯文本答案为止。就这么简单 —— 没有魔法，只有循环。

技能选择是两级路由（技能多了也不怕）：
    第一步「召回」：本地 BM25 检索（零 token），从全部技能里粗筛出 top-k 候选；
    第二步「精排」：只把候选清单交给模型，让它挑一个最合适的（或都不选）。
    兜底：命中会打印理由；交互里可用 /use 技能名 手动指定、/use off 恢复自动。

运行方式：
    python agent.py                          # 交互模式（连续对话）
    python agent.py --question "现在几点了？"
    python agent.py --skill writing-outline  # 强制指定技能再交互
模型怎么配：编辑同目录的 config.ini 即可（Key / 接口地址 / 模型名 / 温度 / 轮数），
也可以临时用环境变量覆盖：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
没有 Key 时自动进入 MockLLM 离线模拟模式，一样能看到完整循环。
交互命令：/skills 实时看技能、/use [名字|off] 手动指定或恢复、exit 退出。
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
import time

import tools
import config as cfgmod  # config.py：读 config.ini
import workspace         # 会话档案化（agent_runs/<时间戳_主题>/）
from llm import LLM, LLM_API_KEY, MockLLM

CFG = cfgmod.load_config()                     # 模型配置（改 config.ini 后重启生效）
MAX_ROUNDS = int(CFG["agent"]["max_rounds"])   # 普通任务轮数预算（config.ini [agent]）
RESEARCH_MAX_ROUNDS = int(CFG.get("research", {}).get("max_rounds", "20"))
# 查资料类任务（deep-research）的独立预算：联网检索天生要多轮，
# 不能让「8 轮硬顶」把研究员拦在半路（config.ini [research] 可调）
SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
TOP_K = 5                                      # 检索粗筛后最多给精排模型看几个候选
_ACTIVE_MARK = "已自动加载技能「"                # 注入消息里的标记，用于查重/延续

SYSTEM_PROMPT = """你是一个乐于助人的中文智能助手，名叫「小智」。
为了回答用户的问题，你可以调用下面这些工具（只有通过工具得到的事实才是可信的，
不要编造工具结果）：
- get_time：查询当前日期时间
- calculator：做数学计算（复杂一点的算式请务必用它，不要心算）
- save_memo / list_memos：帮用户记备忘、回顾备忘
- get_weather：查天气（注意：该工具返回模拟数据，请如实告知用户）
- web_search：联网搜索网页「标题+网址+摘要」（查资料/调研时用）
- fetch_page：抓取公网网页正文（精读搜索结果里的链接用）

使用规则：
1. 需要工具时，一次只做必要的一步，用中文简要说明你正在做什么；
2. 工具返回结果后，阅读结果再决定下一步；结果异常时如实告诉用户；
3. 当你认为问题已经解决，直接输出最终回答，不要画蛇添足地总结工具调用过程。"""


def _emit(emit, payload: dict) -> None:
    """如果调用方给了 emit 回调（Web 界面在听），就把事件发出去；纯命令行则无事发生。

    回调本身可能抛异常（比如浏览器已断开连接），这里一律吞掉 ——
    事件通道坏了不能影响主循环干活。
    """
    if emit is not None:
        try:
            emit(payload)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 输出分级与运行统计（A3）：--quiet 只留关键行；每轮/每次运行自动记 stats
# ---------------------------------------------------------------------------
_QUIET = False


def _info(text: str) -> None:
    """详情行：默认打印，--quiet 模式下隐藏（最终回答与摘要永远打印）。"""
    if not _QUIET:
        print(text)


def _absorb_reply(stats: dict, reply: dict) -> None:
    """把一次 LLM 调用的消耗记入 stats（usage 来自 llm.py 透传的 API 用量）。"""
    stats["llm_calls"] = stats.get("llm_calls", 0) + 1
    usage = reply.get("usage") or {}
    if usage:
        stats["tokens_known"] = True
        stats["prompt_tokens"] = (stats.get("prompt_tokens", 0)
                                  + int(usage.get("prompt_tokens") or 0))
        stats["completion_tokens"] = (stats.get("completion_tokens", 0)
                                      + int(usage.get("completion_tokens") or 0))


def _new_stats() -> dict:
    return {"llm_calls": 0, "tool_calls": 0, "rounds": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "tokens_known": False}


def print_summary(label: str, stats: dict, extra: str = "") -> None:
    """运行摘要行（A3）：quiet 模式下也照常打印。"""
    if stats.get("tokens_known"):
        tok = f"入 {stats.get('prompt_tokens', 0)} / 出 {stats.get('completion_tokens', 0)}"
    else:
        tok = "未知（模拟/旧大脑不计 token）"
    elapsed = stats.get("elapsed")
    dur = f"{elapsed:.1f} 秒" if isinstance(elapsed, (int, float)) else "—"
    print(f"\n[运行摘要] {label}｜LLM 调用 {stats.get('llm_calls', 0)} 次｜"
          f"工具调用 {stats.get('tool_calls', 0)} 次｜轮数 {stats.get('rounds', 0)}｜"
          f"tokens {tok}｜耗时 {dur}{extra}")


def run_agent(llm, question: str, messages: list | None = None,
              max_rounds: int | None = None, system_prompt: str = SYSTEM_PROMPT,
              auto_skills: bool = True, prefer_skill: str | None = None,
              emit=None, stats: dict | None = None,
              ) -> tuple[str, list]:
    """跑完一整轮任务，返回 (最终答案, 完整对话历史)。

    messages 为 None 时开新对话（用 system_prompt 作为人设）；传入历史则接着聊。
    system_prompt 可传入「基础人设 + 强制技能」的组合（见 _build_system_prompt）。
    auto_skills=True 时：先「召回（BM25 粗筛）+ 精排（模型选）」，命中就把技能正文
    注入上下文（模型会在回答里告诉你用了哪个），没命中就用基础人设。
    prefer_skill：会话级手动指定（/use），绕过自动路由直接加载该技能。
    轮数预算：不传时按任务类型自动定 —— 命中 deep-research（查资料类）用
    [research] max_rounds（默认 20），其余用 [agent] max_rounds（默认 8）。
    预算用尽时不会空手而归：会做一次「无工具的收尾总结」，把已收集到的
    内容整理成部分交付物（并如实标注不完整）。
    同技能在同一次会话里不会重复注入（延续生效，省 token、省上下文）。
    emit：可选的「现场直播」回调。每发生一件值得看的事（路由命中技能 / 大脑决定调工具 /
    工具返回 / 给出最终答案）就调用一次 emit({...带 type 字段的小字典...})，各调用点
    下方有注释。命令行用不到它（默认 None = 纯打印）；Web 界面（web_server.py）靠它
    把每一步实时推送给浏览器。回调抛异常会被吞掉，绝不影响主循环。
    注意：对话历史就是「记忆」的最小形态 —— 大模型本身不记任何东西，
    是我们把每轮消息都原样发给它，它才显得「记得」。
    """
    if messages is None:
        messages = [{"role": "system", "content": system_prompt}]

    # ---- 运行统计（A3）：调用方传入 stats 则就地累计，没传就自建 ----
    if stats is None:
        stats = {}
    stats.setdefault("started", time.perf_counter())
    for _k in ("llm_calls", "tool_calls", "rounds", "prompt_tokens", "completion_tokens"):
        stats.setdefault(_k, 0)
    stats.setdefault("tokens_known", False)

    catalog = _skill_catalog() if (auto_skills or prefer_skill) else []
    chosen = None
    origin = ""
    if prefer_skill:  # 1) 手动指定优先
        if any(c["name"] == prefer_skill for c in catalog):
            chosen, origin = prefer_skill, "手动指定(/use)"
        else:
            _info(f"  [技能路由] 手动指定的技能「{prefer_skill}」不存在，"
                  + ("回到自动路由判断" if auto_skills else "本次不加载技能"))
    if chosen is None and auto_skills:  # 2) 自动路由：召回 + 精排
        chosen = _route_skill(llm, question, catalog, stats)
        if chosen:
            origin = "检索粗筛(模拟大脑)" if isinstance(llm, MockLLM) else "检索粗筛+模型精排"
    if chosen:
        if chosen in _active_skills(messages):
            _info(f"  [技能路由] 技能「{chosen}」已在本会话生效，沿用（不重复注入）")
            _emit(emit, {"type": "skill", "name": chosen, "origin": origin,
                         "state": "active"})  # 事件：技能命中但早已生效，无需重复注入
        else:
            try:
                body = _skill_activated_message(chosen)  # 现读磁盘，容忍文件刚好被删
            except RuntimeError as e:
                _info(f"  [技能路由] 加载技能「{chosen}」失败（{e}），本次按基础人设回答")
                _emit(emit, {"type": "notice", "level": "error",
                             "message": f"加载技能「{chosen}」失败：{e}，本次按基础人设回答"})
            else:
                info = next((c for c in catalog if c["name"] == chosen), {})
                terms = _match_terms(question, info)
                reason = "、".join(terms) if terms else "意图匹配"
                _info(f"  [技能路由] {origin}：命中「{chosen}」（理由: 命中“{reason}”），已加载")
                _emit(emit, {"type": "skill", "name": chosen, "origin": origin,
                             "reason": reason, "state": "loaded"})  # 事件：命中并注入技能正文
                messages.append({"role": "system", "content": body})

    # ---- 轮数预算：显式指定 > 按任务类型自动定 ----
    budget = max_rounds if max_rounds is not None else (
        RESEARCH_MAX_ROUNDS if chosen == "deep-research" else MAX_ROUNDS)

    messages.append({"role": "user", "content": question})

    final_answer = ""
    used_rounds = 0
    for step in range(1, budget + 1):
        used_rounds = step
        reply = llm.chat(messages, tools=tools.TOOL_SCHEMAS)
        _absorb_reply(stats, reply)
        calls = reply.get("tool_calls")

        if not calls:  # 大脑没有要求调用工具 -> 说出的话就是最终答案
            final_answer = reply.get("content") or "（模型没有给出回答）"
            break

        # ---- 把这次「调用请求」写进历史，再逐个执行 ----
        assistant_msg = {
            "role": "assistant",
            "content": reply.get("content"),
            "tool_calls": [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {"name": c["name"],
                                 "arguments": json.dumps(c["arguments"], ensure_ascii=False)},
                }
                for c in calls
            ],
        }
        messages.append(assistant_msg)
        # 事件：大脑这一轮的「话 + 工具调用请求」（Web 界面据此画出第 N 轮）
        _emit(emit, {"type": "round", "round": step,
                     "content": reply.get("content"),
                     "tool_calls": [{"id": c["id"], "name": c["name"],
                                     "arguments": c["arguments"]} for c in calls]})
        for c in calls:
            args_preview = json.dumps(c["arguments"], ensure_ascii=False)
            _info(f"  [第{step}轮] 大脑决定调用工具: {c['name']}({args_preview})")
            _emit(emit, {"type": "tool", "round": step, "id": c["id"],
                         "name": c["name"], "arguments": c["arguments"]})  # 事件：开始执行工具
            result = tools.run_tool(c["name"], c["arguments"])
            stats["tool_calls"] += 1
            _info(f"           工具返回: {result[:120]}{'...' if len(result) > 120 else ''}")
            _emit(emit, {"type": "tool_result", "round": step, "id": c["id"],
                         "name": c["name"], "result": result})  # 事件：工具真实返回
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
    else:
        # 预算用尽：不空手而归 —— 禁止再调工具，让模型把已收集到的内容
        # 做一次「收尾总结」，交出一份标注了不完整性的部分交付物
        _info(f"  [预算用尽] 已达 {budget} 轮上限，正在对已收集内容做收尾总结…")
        _emit(emit, {"type": "notice", "level": "warn",
                     "message": f"已达 {budget} 轮上限，正在对已收集内容做收尾总结…"})
        wrap_messages = list(messages) + [{
            "role": "user",
            "content": "轮数预算已用尽。请只基于上面已经拿到的内容做一次收尾总结："
                       "能确认的信息整理成回答（按技能要求给《原始资料》或交付物），"
                       "未能覆盖的缺口明确列出；不要再调用工具，不要编造。",
        }]
        try:
            reply = llm.chat(wrap_messages, tools=[])
            _absorb_reply(stats, reply)
            wrap_text = reply.get("content") or "（收尾总结未给出内容）"
        except Exception as e:
            wrap_text = f"（收尾总结失败：{e}。以下是最接近完整的一次工具返回）"
        final_answer = ("（预算用尽，以下为收尾交付，可能不完整）\n\n" + wrap_text)
    stats["rounds"] = stats.get("rounds", 0) + used_rounds
    stats["elapsed"] = time.perf_counter() - stats["started"]

    # 事件：最终答案出炉（Web 界面据此渲染回答区）
    _emit(emit, {"type": "answer", "content": final_answer})
    # 最终答案也写进历史 —— 下一次提问时，模型才能「记得」这次回答过什么
    messages.append({"role": "assistant", "content": final_answer})

    # 存一份完整对话流水账，方便你事后研究每一轮发了什么
    transcript = os.path.join(tools.BASE_DIR, "last_transcript.json")
    with open(transcript, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)
    return final_answer, messages


def _load_skill(name: str) -> str:
    """读取 skills/<名字>.md 的技能内容（自动跳过开头的 --- 元信息块）。"""
    path = os.path.join(SKILLS_DIR, f"{name}.md")
    if not os.path.exists(path):
        available = sorted(
            os.path.splitext(os.path.basename(p))[0]
            for p in glob.glob(os.path.join(SKILLS_DIR, "*.md"))
        )
        raise RuntimeError(f"找不到技能文件 skills/{name}.md。可用技能: {', '.join(available) or '（无）'}")
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2].lstrip()
    return text


def _build_system_prompt(skill: str | None) -> str:
    """基础人设 + （可选）强制技能内容 = 最终 system prompt。

    技能的本质：一段可复用的「操作手册」，加载时拼进 system prompt。
    模型并不会「学会」技能，它只是照着更长的说明书工作。
    """
    prompt = SYSTEM_PROMPT
    if skill:
        prompt += f"\n\n===== 已加载技能：{skill} =====\n{_load_skill(skill)}"
    return prompt


# ---------------------------------------------------------------------------
# 技能元信息与目录（热插拔：每次提问都现读磁盘，从不缓存）
# ---------------------------------------------------------------------------
_FM_KEY = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(.*)$")


def _parse_frontmatter(lines: list[str]) -> dict:
    """解析技能文件开头的 --- 元信息块，支持单行值与多行列表：

        description: 一句话          -> 单行
        keywords: a, b               -> 单行逗号列表
        intents:                     -> 多行列表（每个 "- " 开头一行）
          - 想写文章帮我出大纲
          - 帮我想标题
    """
    meta: dict = {}
    if not lines or lines[0].strip() != "---":
        return meta
    current = None          # 当前正在收集的多行列表键
    collected: list[str] = []
    for line in lines[1:]:
        s = line.strip()
        if s == "---":
            break
        if not s:                       # 空行：结束多行收集
            if current and collected:
                meta[current] = collected
            current, collected = None, []
            continue
        m = _FM_KEY.match(line)
        if m:                           # 新键
            if current and collected:
                meta[current] = collected
            key, rest = m.group(1).lower(), m.group(2).strip().strip("\"'")
            if rest:
                if key in ("keywords", "intents", "avoid_when"):
                    meta[key] = [x.strip() for x in re.split(r"[，,、]", rest) if x.strip()]
                else:
                    meta[key] = rest
                current, collected = None, []
            else:
                current, collected = key, []
        elif current:                   # 多行列表的条目
            collected.append(s[2:].strip() if s.startswith("- ") else s)
    if current and collected:
        meta[current] = collected
    return meta


def _skill_catalog() -> list[dict]:
    """实时扫描 skills/*.md —— 每次调用都现读磁盘，这是「热插拔」的关键：
    运行中新增 / 修改 / 删除技能文件，下一次路由立即看到新状态，无需重启。

    返回 [{name, description, keywords, intents, avoid_when}]：
      description  真实模型精排时判断用（一句话）
      keywords     离线模拟路由与检索的强关键词（可选）
      intents      用户可能的说法范例（可选，提升召回与精排准确率）
      avoid_when   什么时候不要用（可选，检索命中会扣分，防误选）
    """
    catalog = []
    for path in sorted(glob.glob(os.path.join(SKILLS_DIR, "*.md"))):
        name = os.path.splitext(os.path.basename(path))[0]
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        meta = _parse_frontmatter(lines)
        catalog.append({
            "name": name,
            "description": meta.get("description", ""),
            "keywords": meta.get("keywords", []),
            "intents": meta.get("intents", []),
            "avoid_when": meta.get("avoid_when", []),
        })
    return catalog


# ---------------------------------------------------------------------------
# 第一步「召回」：本地 BM25 检索（零依赖、零 token）
# ---------------------------------------------------------------------------
# 常见虚词/万能词（"帮我"、"我想"这类几乎所有请求都会带，没有检索区分度）
_STOP_TOKENS = {
    "帮我", "我想", "你要", "请你", "给我", "请问", "想要", "需要",
    "可以", "一下", "这个", "那个", "什么", "怎么", "如何", "为什么",
    "的话", "还有", "然后", "但是", "因为", "所以", "如果", "就是",
    "一个", "这样", "那样", "知道", "觉得", "告诉", "回答", "问题",
}


def _tokenize(text: str) -> list[str]:
    """极简中文分词（零依赖，够检索用）：英文/数字整词 + 汉字二元组。

    单字与虚词（如「帮我」）会造成大量误命中，一律不过索引；
    双字词才有区分度（「文章」「润色」），这是「检索宁缺毋滥」的体现。
    """
    text = (text or "").lower()
    tokens = [t for t in re.findall(r"[a-z0-9_]+", text) if t not in _STOP_TOKENS]
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))  # 双字词
        if len(run) == 1:                                          # 单字整句兜底
            tokens.append(run)
    return [t for t in tokens if t not in _STOP_TOKENS]


def _bm25_score(query: list[str], doc: list[str], df: dict, n_docs: int,
                avg_dl: float) -> float:
    """对单个文档算 BM25 分数（k1=1.5, b=0.75 是常用默认值）。"""
    from collections import Counter
    dl = len(doc)
    if not query or not dl:
        return 0.0
    tf = Counter(doc)
    k1, b = 1.5, 0.75
    score = 0.0
    for t in set(query):
        f = tf.get(t, 0)
        if not f:
            continue
        idf = math.log(1 + (n_docs - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
        score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avg_dl))
    return score


def _retrieve_candidates(catalog: list[dict], question: str,
                         top_k: int = TOP_K) -> list[dict]:
    """BM25 粗筛：返回打分后取前 top_k 的候选技能（分数须 > 0）。

    正向字段：名字/简介/keywords/intents（keywords 与 intents 加权 2 倍）；
    负向字段：avoid_when（命中会扣 0.8 倍分，用来防「润色误选写作梳理」这类错误）。
    全部候选分数 <= 0 说明召回为空 —— 精排模型连看都不用看。
    """
    q = _tokenize(question)
    if not q:
        return []
    pos_docs, neg_docs, entries = [], [], []
    for c in catalog:
        pos = " ".join([
            c["name"], c["description"],
            " ".join(c["keywords"]) * 2,   # 关键词加权：重复两次提高 tf
            " ".join(c["intents"]) * 2,
        ])
        neg = " ".join(c["avoid_when"])
        pt, nt = _tokenize(pos), _tokenize(neg)
        entries.append((c, pt, nt))
        pos_docs.append(pt)
        neg_docs.append(nt)

    df: dict = {}
    for doc in pos_docs:
        for t in set(doc):
            df[t] = df.get(t, 0) + 1
    n = max(len(pos_docs), 1)
    avg_dl = sum(len(d) for d in pos_docs) / n

    scored = []
    for c, pt, nt in entries:
        s = _bm25_score(q, pt, df, n, avg_dl)
        if nt:
            s -= 0.8 * _bm25_score(q, nt, df, n, avg_dl)   # 命中「不适用」扣分
        if s > 0:
            scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]


def _match_terms(question: str, c: dict, limit: int = 4) -> list[str]:
    """命中理由：取问题里与该技能正文字面重合的词（>=2 字或整词），最多 limit 个。"""
    pos = set(_tokenize(" ".join([
        c.get("name", ""), c.get("description", ""),
        " ".join(c.get("keywords", [])), " ".join(c.get("intents", [])),
    ])))
    terms: list[str] = []
    for t in _tokenize(question):
        if t in pos and t not in terms and len(t) >= 2:
            terms.append(t)
        if len(terms) >= limit:
            break
    return terms


def _active_skills(messages: list[dict]) -> list[str]:
    """从对话历史找出已注入生效的技能名（用于同技能延续、避免重复注入）。"""
    out: list[str] = []
    for m in messages:
        if m.get("role") == "system":
            for hit in re.findall(rf"{_ACTIVE_MARK}([^」]+)」", m.get("content", "")):
                if hit not in out:
                    out.append(hit)
    return out


# ---------------------------------------------------------------------------
# 第二步「精排」：把 top-k 候选交给模型挑（只有这一步花 token）
# ---------------------------------------------------------------------------
_ROUTER_SYSTEM = """你是技能路由器。根据用户的【问题内容】，从下面给出的【候选技能】里
挑选一个最合适的启用（也可以一个都不选）。

规则：
1. 只从候选技能里选；某个候选与问题明显相关且能帮上忙才启用；
2. 候选都不沾边（闲聊、问时间、算数等）输出 NO_SKILL；
3. 两个候选都像时，只选最像的一个，不要贪多；
4. 「不适用」里描述的情形命中用户请求时，不要选该技能。

输出格式（只输出一行，不要任何解释）：
- 启用：USE_SKILL:技能名
- 不启用：NO_SKILL"""


def _route_skill(llm, question: str, catalog: list[dict],
                 stats: dict | None = None) -> str | None:
    """两级路由的第二步：返回命中的技能名；没命中/出错返回 None。

    先 _retrieve_candidates 粗筛（在 run_agent 里已算过 catalog，这里直接用）——
    但为了模块可独立使用，catalog 为空时自己扫一次。
    stats：可选，路由的 LLM 调用也会记入统计。
    """
    if not catalog:
        catalog = _skill_catalog()
    if not catalog:
        return None
    candidates = _retrieve_candidates(catalog, question)
    if not candidates:
        return None  # 召回为空：连模型都不用问

    allowed = {c["name"] for c in candidates}
    if isinstance(llm, MockLLM):  # 模拟大脑：不做语义精排，取检索第一名（规则 = 极简路由器）
        return candidates[0]["name"]

    lines = []
    for c in candidates:
        line = f"- {c['name']}：{c['description'] or '（无描述）'}"
        if c["intents"]:
            line += f"\n    适用示例：{'；'.join(c['intents'][:2])}"
        if c["avoid_when"]:
            line += f"\n    不适用：{'；'.join(c['avoid_when'][:2])}"
        lines.append(line)

    router_messages = [
        {"role": "system", "content": _ROUTER_SYSTEM + "\n\n候选技能（共 "
                                      f"{len(candidates)} 个，从 {len(catalog)} 个技能中粗筛出）：\n"
                                      + "\n".join(lines)},
        {"role": "user", "content": question},
    ]
    try:
        reply = llm.chat(router_messages, tools=[])
        if stats is not None:
            _absorb_reply(stats, reply)
    except Exception as e:
        _info(f"  [技能路由] 路由请求失败（{e}），跳过技能直接回答")
        return None
    text = reply.get("content") or ""

    m = re.search(r"USE_SKILL\s*[:：]?\s*([A-Za-z0-9_-]+)", text)
    if m and m.group(1) in allowed:
        return m.group(1)
    for c in candidates:  # 宽容处理：模型没按格式来但回复里出现了某个候选名
        if c["name"] in text and "NO_SKILL" not in text.upper():
            return c["name"]
    return None


def _skill_activated_message(name: str) -> str:
    """命中技能后注入上下文的系统消息：要求模型告知用户 + 给出技能正文。"""
    body = _load_skill(name)
    return (f"[系统] 你{_ACTIVE_MARK}{name}」。请在本轮回答开头用一句话告诉用户："
            f"本次使用了技能「{name}」。然后严格按下面的技能手册工作：\n\n{body}")


def _pick_llm(args) -> tuple:
    """选择「大脑」并报告 Key 来源。Key 优先级：命令行 > 环境变量 > config.ini > llm.py 常量。
    base_url / model 优先级：环境变量 > config.ini > 默认值。
    全都没有 Key 就退回离线模拟大脑（MockLLM），不会在问答前弹窗。
    """
    def env(name: str) -> str:
        return os.environ.get(name, "").strip()

    if args.force_mock:  # 显式要求离线模拟 -> 不管有没有 Key 都用 MockLLM
        return MockLLM(), None

    m = CFG["model"]
    api_key = (args.api_key or env("DEEPSEEK_API_KEY")
               or m.get("api_key", "").strip() or LLM_API_KEY.strip())
    if api_key:
        if args.api_key:
            source = "命令行 --api-key"
        elif env("DEEPSEEK_API_KEY"):
            source = "环境变量 DEEPSEEK_API_KEY"
        elif m.get("api_key", "").strip():
            source = "config.ini"
        else:
            source = "llm.py 顶部常量 LLM_API_KEY"
        return LLM(
            api_key=api_key,
            base_url=env("DEEPSEEK_BASE_URL") or m.get("base_url", "https://api.deepseek.com"),
            model=env("DEEPSEEK_MODEL") or m.get("model", "deepseek-chat"),
            temperature=float(m.get("temperature", "0.7")),
        ), source
    print("  [提示] 未配置 API Key，本次使用离线模拟大脑。填 Key 的 4 种方式（详见 README）：")
    print("           1. 配置文件: 编辑 config.ini，[model] 区的 api_key 行填上 Key（推荐）")
    print("           2. 环境变量: set DEEPSEEK_API_KEY=sk-xxxx （永久: setx ...）")
    print("           3. 代码常量: llm.py 顶部 LLM_API_KEY = \"sk-xxxx\"")
    print("           4. 命令行:   python agent.py -q \"...\" --api-key sk-xxxx")
    return MockLLM(), None


def main():
    parser = argparse.ArgumentParser(description="Agent MVP —— 零第三方依赖的 ReAct 智能体")
    parser.add_argument("--question", "-q", help="问一个问题然后退出（不带则进入连续对话模式）")
    parser.add_argument("--api-key", help="DeepSeek API Key（也可用环境变量 DEEPSEEK_API_KEY）")
    parser.add_argument("--force-mock", action="store_true", help="强制使用离线模拟大脑")
    parser.add_argument("--quiet", action="store_true",
                        help="只保留问答/摘要等关键输出（隐藏路由/工具细节）")
    parser.add_argument("--skill", metavar="名字",
                        help="强制加载 skills/ 目录下的技能，如 writing-outline（写作思路梳理）")
    parser.add_argument("--check-key", action="store_true",
                        help="只自检 Key/接口是否连通（不消耗 token），测完即退出")
    args = parser.parse_args()

    global _QUIET  # 输出分级：--quiet 后详情行全部隐藏（A3）
    _QUIET = args.quiet

    # 技能加载失败（名字写错）就立刻提示，而不是等到问答时才发现
    try:
        system_prompt = _build_system_prompt(args.skill)
    except RuntimeError as e:
        print(f"\n[错误] {e}")
        return

    _info("=" * 60)
    _info(" Agent MVP：大模型 + 工具调用的 ReAct 循环")
    _info(" 每轮循环 = 大脑思考 -> 调用工具 -> 观察结果 -> 再思考")
    _info("=" * 60)

    llm, key_source = _pick_llm(args)
    if isinstance(llm, LLM):
        _info(" 当前大脑: LLM（真实模型，配置来自 config.ini / 环境变量）")
        _info(f"   模型: {llm.model}")
        _info(f"   接口: {llm.base_url}")
        _info(f"   温度: {llm.temperature}")
        _info(f"   Key: 已配置（来源: {key_source}）")
    else:
        _info(" 当前大脑: MockLLM（离线模拟，无 API Key）")
    catalog = _skill_catalog()
    if args.skill:
        _info(f" 技能: {args.skill}（强制指定，已注入 system prompt）")
    elif catalog:
        _info(f" 技能: 自动路由（两级：BM25 粗筛 top{TOP_K} + 模型精排；"
              f"现有 {len(catalog)} 个技能）")
    _info("")

    if args.check_key:
        if isinstance(llm, MockLLM):
            print(" [自检] 当前没有可用 Key（未配置），无法连接真实模型。")
            print("        请先填 Key（4 种方式见上方提示 / README），再运行 python agent.py --check-key")
            return
        print(f" [自检] 正在连接 {llm.base_url} 验证 Key 是否有效 ...")
        try:
            models = llm.ping()
        except RuntimeError as e:
            print(f"\n [自检失败] {e}")
            return
        shown = ", ".join(models[:8]) if models else "（接口未返回模型列表）"
        print(f"\n [自检通过] Key 有效！该接口可用的模型: {shown}")
        return

    # ---- 会话档案化辅助（A2）：每次问答/会话落独立档案目录 ----
    def _save_archive(llm_, key_src, args_, meta: dict, history, topic: str):
        if not history:
            return
        meta = dict(meta)
        meta["llm"] = type(llm_).__name__
        meta["model"] = getattr(llm_, "model", "")
        meta["key_source"] = key_src
        meta["skill_mode"] = args_.skill or "auto"
        path = workspace.archive_session(meta, history, topic=topic)
        print(f" 会话档案：{path}")

    if args.question:
        print(f" 你: {args.question}")
        run_stats = _new_stats()
        answer, hist = run_agent(llm, args.question, system_prompt=system_prompt,
                                 auto_skills=args.skill is None, stats=run_stats)
        print(f"\n 小智: {answer}")
        _save_archive(llm, key_source, args,
                      meta={"mode": "one-shot", "question": args.question}, history=hist,
                      topic=args.question)
        print_summary("单次问答", run_stats)
        return

    # ---- 连续对话模式 ----
    # 关键点：把上一轮返回的 messages 原样传给下一轮 run_agent，
    # 模型就能「记住」前面聊过什么 —— 这就是 Agent 记忆的最基本形态。
    history = None
    prefer: str | None = None   # /use 手动指定的会话级技能；None = 自动路由
    auto_on = args.skill is None
    q_count = 0
    first_topic = ""
    session_stats = _new_stats()  # 整个会话的累计统计（A3 摘要用）
    _info(" 进入连续对话（exit 退出；/skills 看技能；/use 名字 手动指定技能）。"
          "试试：「现在几点了？」")
    while True:
        try:
            question = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            if q_count:
                _save_archive(llm, key_source, args,
                              {"mode": "chat", "questions": q_count}, history,
                              topic=first_topic or "会话")
                print_summary(f"会话（{q_count} 问）", session_stats)
            break
        if not question:
            continue
        if question.lower() in {"exit", "quit", "退出"}:
            print("再见！")
            if q_count:
                _save_archive(llm, key_source, args,
                              {"mode": "chat", "questions": q_count}, history,
                              topic=first_topic or "会话")
                print_summary(f"会话（{q_count} 问）", session_stats)
            break
        if question in {"/skills", "技能列表"}:  # 实时查看当前技能（热插拔后立刻可见）
            cats = _skill_catalog()
            if not cats:
                print("  当前没有可用技能（skills/ 目录为空，放一个 .md 进去即可）。")
            for c in cats:
                extras = []
                if c["keywords"]:
                    extras.append("关键词: " + "、".join(c["keywords"]))
                if c["intents"]:
                    extras.append("适用: " + "；".join(c["intents"][:2]))
                tail = f"（{' | '.join(extras)}）" if extras else ""
                print(f"  - {c['name']}：{c['description'] or '（无描述）'}{tail}")
            print("  提示：目录增删改无需重启；/use 名字 可手动指定，/use off 恢复自动。")
            continue
        if question == "/use" or question.startswith("/use "):
            arg = question.split(maxsplit=1)[1].strip() if " " in question else ""
            if not arg:
                if prefer:
                    print(f"  当前手动指定技能：{prefer}（/use off 恢复自动路由）")
                else:
                    print("  当前为自动路由（未手动指定）。/use 技能名 手动指定；"
                          "/use off 恢复自动。")
            elif arg in {"off", "关", "none", "无"}:
                prefer = None
                print("  已恢复自动路由。（此前注入过会话的技能正文仍留在上下文中，"
                      "想完全清空请退出重开。）")
            else:
                names = sorted(c["name"] for c in catalog)
                if arg in names:
                    prefer = arg
                    print(f"  已手动指定技能「{arg}」：之后每轮直接加载它，"
                          "不再自动路由（/use off 恢复）。")
                else:
                    print(f"  没有技能「{arg}」。可用: {'、'.join(names) or '（无）'}"
                          "（/use off 恢复自动路由）")
            continue
        if question.startswith("/"):
            print(f"  未知命令：{question}（支持 exit、/skills、/use [名字|off]）")
            continue
        q_count += 1
        first_topic = first_topic or question
        answer, history = run_agent(llm, question, messages=history,
                                    system_prompt=system_prompt,
                                    auto_skills=auto_on,
                                    prefer_skill=prefer if auto_on else None,
                                    stats=session_stats)
        print(f"\n 小智: {answer}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"\n[错误] {e}", file=sys.stderr)
        sys.exit(1)
