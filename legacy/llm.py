# -*- coding: utf-8 -*-
"""
llm.py —— 智能体的「大脑」

主循环(agent.py)只认识一个接口：chat(messages, tools) -> 模型回复。
回复有两种可能：
    1. 普通文本 content        -> 这就是最终答案，循环结束
    2. 请求调用工具 tool_calls -> 主循环去执行工具，把结果喂回去，再问一次大脑

本文件提供两种「大脑」实现，接口完全一致，可任意替换：
    * LLM      —— 真正的 DeepSeek（或任何 OpenAI 兼容接口），走 HTTP 调用
    * MockLLM  —— 没有 API Key 也能跑的「模拟大脑」：
                   用几条 if/else 规则假装会思考。规则 = 一个极简的模型，
                   它帮你理解循环的运转，理解后换真模型即可。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

# ===========================================================================
# 【填 Key 的地方 —— 最简单（适合本机学习）】
# 把下面这对引号中间换成你的 Key，例如：LLM_API_KEY = "sk-你的Key"
# 填好后直接运行 python agent.py，顶部显示「当前大脑: LLM」即已生效。
# 注意：含 Key 的文件不要发给别人 / 上传 Git（Key 等同你的余额）。
# ===========================================================================
LLM_API_KEY = ""

# ---------------------------------------------------------------------------
# 真正的 LLM：DeepSeek API（OpenAI 兼容的 chat completions 接口）
# ---------------------------------------------------------------------------
class LLM:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-chat", temperature: float = 0.7):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature

    def ping(self) -> list[str]:
        """自检：连一次 GET /models，只验证 Key/接口是否连通，不消耗 token。

        成功返回服务器支持的模型名列表；失败抛 RuntimeError（带中文指引）。
        """
        req = urllib.request.Request(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 401:
                tip = ("Key 无效（拼写/复制不完整，或已被删除）：请到 platform.deepseek.com "
                       "新建 Key，用页面上的「复制」按钮完整复制，再填入 config.ini 的 api_key")
            else:
                tip = f"HTTP {e.code}：{body[:200]}"
            raise RuntimeError(f"自检失败（接口 {self.base_url}）：{tip}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"自检失败：连不上 {self.base_url}（{e.reason}），请检查 config.ini 的 base_url") from e
        return [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]

    def chat(self, messages: list[dict], tools: list | None = None) -> dict:
        """发一次请求，返回解析后的模型回复：{content, tool_calls}"""
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools or [],   # 说明书给模型，模型才知道有哪些工具可用
            "tool_choice": "auto",  # auto = 让模型自己决定要不要用工具
            "temperature": self.temperature,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            tip = ""
            if e.code == 401:
                tip = ("\n[指引] API Key 无效或已停用：请到 platform.deepseek.com -> API Keys 重新"
                       "创建 Key，再填入 config.ini 的 api_key（或设置环境变量 DEEPSEEK_API_KEY）。")
            elif e.code == 402:
                tip = "\n[指引] 账户余额不足：请到平台充值后再试。"
            elif e.code == 400 and "model" in body.lower():
                tip = "\n[指引] 模型名或接口地址不对？检查 config.ini 的 model 与 base_url 是否匹配。"
            raise RuntimeError(f"调用大模型失败 HTTP {e.code}：{body}{tip}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"无法连接大模型接口：{e.reason}（请检查网络 / base_url）") from e

        msg = data["choices"][0]["message"]
        tool_calls = None
        if msg.get("tool_calls"):
            tool_calls = []
            for tc in msg["tool_calls"]:
                fn = tc["function"]
                try:
                    arguments = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {"_raw": fn.get("arguments")}
                tool_calls.append({
                    "id": tc["id"],
                    "name": fn["name"],
                    "arguments": arguments,  # 已解析成 dict，主循环可直接用
                })
        return {"content": msg.get("content"), "tool_calls": tool_calls,
                "usage": data.get("usage") or {}}


# ---------------------------------------------------------------------------
# 模拟大脑：离线演示 ReAct 循环（关键词 -> 假装调用工具）
# ---------------------------------------------------------------------------
_CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安", "南京", "苏州"]


class MockLLM:
    """没有 API Key 时的教学用大脑。

    工作原理：翻看对话历史，最后一条如果是「工具返回结果」，就据此编一段最终回答；
    否则在用户问题里找关键词，命中就返回一次工具调用请求。
    真实的 LLM 靠海量训练学到的是「概率」，这里靠 if/else —— 但循环的运转完全一样。
    """

    def chat(self, messages: list[dict], tools: list | None = None) -> dict:
        # ---- 1) 如果上一条消息是工具结果 -> 扮演「看完结果后作答」的模型 ----
        if messages and messages[-1].get("role") == "tool":
            # 把连续的最后几条 tool 结果都收集起来（模型可能一次调用了多个工具）
            results = []
            for m in reversed(messages):
                if m.get("role") == "tool":
                    results.append(m["content"])
                elif m.get("tool_calls"):
                    break
            results.reverse()
            summary = "\n".join(f"- {r}" for r in results)
            return {"content": f"工具已返回内容：\n{summary}", "tool_calls": None}

        # ---- 2) 否则，从最近的用户问题里匹配规则，可能一次要求调用多个工具 ----
        text = ""
        for m in reversed(messages):
            if m.get("role") == "user" and m.get("content"):
                text = m["content"]
                break
        text = text.replace("，", " ").replace("？", " ").replace("!", " ")
        for zh, en in [("乘以", "*"), ("除以", "/"), ("加上", "+"), ("减去", "-")]:
            text = text.replace(zh, en)

        # 长文本（流水线各阶段的整段资料/成稿）不做关键词路由：
        # 离线模式只演示流程，逐词扫描长文极易误触发，交给真实模型处理
        if len(text) > 200:
            return {"content": "（模拟大脑）这是一段较长的输入，离线模式不做逐词路由。"
                                "配置 API Key 后由真实模型处理。",
                    "tool_calls": None}

        calls = []  # 一次可请求多个工具（真实 LLM 也会这么做，例如先查时间再计算）
        seq = {"n": 0}

        def call(name: str, arguments: dict) -> None:
            seq["n"] += 1
            calls.append({"id": f"mock_{seq['n']}", "name": name, "arguments": arguments})

        if any(k in text for k in ["天气", "气温", "冷不冷", "热不热"]):
            city = next((c for c in _CITIES if c in text), "北京")
            call("get_weather", {"city": city})
        if any(k in text for k in ["几点了", "几点", "时间", "日期", "今天星期"]):
            call("get_time", {})

        # 联网检索类请求：模拟大脑也走一遍「工具调用」完整回路。
        # web_search 本身会真实联网；网络不通时工具如实返回错误文本 ——
        # 正好演示「工具失败要如实报告，而不是编造资料」。
        if any(k in text for k in ["搜索", "查一下", "查资料", "调研", "联网",
                                   "检索", "搜一下", "找资料"]):
            q = text.split("主题", 1)[1].lstrip("：: ") if "主题" in text else text
            for sep in ("\n", "。", "；", "，", " "):
                i = q.find(sep)
                if i > 0:
                    q = q[:i]
                    break
            call("web_search", {"query": (q or "教学演示").strip()[:60]})
            return {"content": None, "tool_calls": calls}

        # 「回顾/列出」优先于「保存」，避免“列一下备忘”被误解成存备忘
        if any(k in text for k in ["备忘本", "列表", "列出来", "我记过", "记过什么", "回顾", "之前记的"]):
            call("list_memos", {})

        if any(k in text for k in ["记住", "记一下", "备忘", "写下来", "记录"]):
            # 把关键词之后的话当作要记的内容
            for k in ["记住：", "记一下：", "记住", "记一下", "备忘：", "写下来：", "记录"]:
                if k in text:
                    content = text.split(k, 1)[1].strip(" ：")
                    break
            else:
                content = ""
            call("save_memo", {"content": content or text})
            # 备忘内容里可能恰好有数字，别误触发计算，直接返回
            return {"content": None, "tool_calls": calls}

        # 计算：取出含数字和运算符的最长片段当作表达式（上面的翻译已作用在 text 上）
        candidates = re.findall(r"[0-9+\-*/%.()\s]+", text)
        candidates = [c.strip() for c in candidates
                      if c.strip() and any(ch.isdigit() for ch in c) and len(c.strip()) <= 30]
        if candidates and any(k in text for k in ["计算", "算", "等于", "多少", "结果"]):
            call("calculator", {"expression": max(candidates, key=len)})

        if calls:
            return {"content": None, "tool_calls": calls}

        return {
            "content": "（模拟大脑）这个问题我用规则回答不了，只能简单回应："
                       "我没有 API Key，正在离线模拟模式运行。配置 DEEPSEEK_API_KEY 后，"
                       "就能让真正的大模型来思考和回答你的问题。",
            "tool_calls": None,
        }
