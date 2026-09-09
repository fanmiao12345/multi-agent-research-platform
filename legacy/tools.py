# -*- coding: utf-8 -*-
"""
tools.py —— 智能体的「手」

智能体和普通聊天机器人最大的区别：它能调用工具（函数）。
这里的每个工具 = 一段普通 Python 函数 + 一份「给大模型看的说明书」(JSON Schema)。

大模型并不会执行代码，它只是根据说明书生成一句话：
    {"name": "calculator", "arguments": "{\"expression\": \"12*34+56\"}"}
真正执行的是主循环（agent.py）里的 run_tool()。

想添加新工具？照着下面的格式加一个函数 + 一份 schema 即可（详见 README）。
"""

from __future__ import annotations

import ast
import datetime
import html as htmlmod
import json
import math
import os
import random
import re
import urllib.error
import urllib.parse
import urllib.request

# 联网工具的公共配置
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
_FETCH_CAP = 512 * 1024        # 单页最多读 512KB，防超大页面
_SEARCH_CAP_CHARS = 4000       # 搜索结果文本上限
_FETCH_CAP_CHARS = 3000        # 抓取的正文文本上限


def _http_get(url: str, timeout: int = 15) -> bytes:
    """GET 一个 URL，非 2xx 抛异常，返回响应体。"""
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = getattr(resp, "status", 200)
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        return resp.read(_FETCH_CAP + 1)


def _strip_html(raw: str) -> str:
    """把 HTML 粗略转成纯文本（去脚本/样式/标签、压缩空白）。"""
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</(p|div|li|h[1-6]|tr)>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = htmlmod.unescape(raw)
    lines = [re.sub(r"[ \t\u3000]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _plain_results(entries: list[tuple[str, str, str]], max_chars: int) -> str:
    """把 [(标题, 网址, 摘要)] 排版成纯文本（给模型看的工具返回）。"""
    parts = []
    for i, (title, url, snippet) in enumerate(entries, 1):
        block = f"{i}. {title}\n   {url}"
        if snippet:
            block += f"\n   {snippet[:180]}"
        parts.append(block)
    text = "\n\n".join(parts)
    return text[:_SEARCH_CAP_CHARS]


def web_search(query: str) -> str:
    """搜索引擎检索（真实联网）：返回若干条「标题 + 网址 + 摘要」。

    优先 Bing（国内可直连），失败时退回 DuckDuckGo lite。解析基于 HTML，
    引擎改版可能导致解析失败 —— 那时会如实返回错误，而不是编造结果。
    """
    q = urllib.parse.quote(query)
    entries: list[tuple[str, str, str]] = []
    for engine, url in (
        ("bing", f"https://www.bing.com/search?q={q}&count=8&setlang=zh-hans"),
        ("ddg", f"https://lite.duckduckgo.com/lite/?q={q}"),
    ):
        try:
            raw = _http_get(url).decode("utf-8", errors="replace")
        except Exception as e:
            continue  # 该引擎不可达/超时，试下一个
        if engine == "bing":
            blocks = re.findall(r'(?is)<li class="b_algo".*?</li>', raw)[:8]
            for b in blocks:
                m = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
                if not m:
                    continue
                href, title = m.group(1), _strip_html(m.group(2)).strip()
                snip = ""
                sm = re.search(r"(?is)<p[^>]*>(.*?)</p>", b)
                if sm:
                    snip = _strip_html(sm.group(1)).strip()
                if title:
                    entries.append((title, href, snip))
        else:  # ddg lite：链接表 + 摘要单元格
            links = re.findall(r'(?is)<a rel="nofollow"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', raw)
            snips = re.findall(r'(?is)<td class="result-snippet"[^>]*>(.*?)</td>', raw)
            for i, (href, title) in enumerate(links[:8]):
                t = _strip_html(title).strip()
                s = _strip_html(snips[i]).strip() if i < len(snips) else ""
                if t:
                    entries.append((t, href, s))
        if entries:
            break
    if not entries:
        return ("（真实联网检索失败：Bing 与 DuckDuckGo 都不可达或未解析出结果。"
                "请检查网络后重试，或人工提供资料链接）")
    return _plain_results(entries, _SEARCH_CAP_CHARS)


def fetch_page(url: str, max_chars: int = _FETCH_CAP_CHARS) -> str:
    """抓取一个网页并转成纯文本正文（含标题），最多 max_chars 字。

    只允许 http/https；出于安全会拒绝 localhost 和内网地址（防 SSRF 的教学示例）。
    """
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return f"错误：只支持 http/https 链接，收到的是 {parsed.scheme or '空'}。"
    host = (parsed.hostname or "").lower()
    blocked = (
        host in ("localhost",) or host.endswith(".local")
        or re.match(r"^(127\.|10\.|192\.168\.|169\.254\.|0\.)", host)
        or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", host)
        or host in ("[::1]", "::1")
    )
    if blocked:
        return f"错误：出于安全考虑拒绝访问内网/本机地址（{host}），只抓公网页面。"
    try:
        raw = _http_get(url)
    except urllib.error.HTTPError as e:
        return f"抓取失败：HTTP {e.code}（{url}）"
    except urllib.error.URLError as e:
        return f"抓取失败：{e.reason}（{url}）"
    except Exception as e:
        return f"抓取失败：{e}（{url}）"
    text = raw.decode("utf-8", errors="replace")
    title = ""
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
    if m:
        title = _strip_html(m.group(1)).strip()
    body = _strip_html(text)
    if title:
        body = f"标题：{title}\n\n{body}"
    if len(body) > max_chars:
        body = body[:max_chars] + "\n…（正文过长已截断）"
    return body or "（页面没有可提取的文本内容）"

# ---------------------------------------------------------------------------
# 小工具：备忘本（演示「状态 / 记忆」——工具不一定是纯函数，可以读写文件）
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEMO_FILE = os.path.join(BASE_DIR, "memos.json")


def _load_memos() -> list[str]:
    if not os.path.exists(MEMO_FILE):
        return []
    try:
        with open(MEMO_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("memos", [])
    except Exception:
        return []


def _save_memos(memos: list[str]) -> None:
    with open(MEMO_FILE, "w", encoding="utf-8") as f:
        json.dump({"memos": memos}, f, ensure_ascii=False, indent=2)


def save_memo(content: str) -> str:
    """把一句话存入备忘本，返回这条备忘的编号。"""
    memos = _load_memos()
    memos.append(content)
    _save_memos(memos)
    return f"已保存为第 {len(memos)} 条备忘，当前共 {len(memos)} 条。"


def list_memos() -> str:
    """列出备忘本里的所有内容。"""
    memos = _load_memos()
    if not memos:
        return "备忘本是空的。"
    return "\n".join(f"{i}. {m}" for i, m in enumerate(memos, 1))


# ---------------------------------------------------------------------------
# 小工具：当前时间
# ---------------------------------------------------------------------------
def get_time() -> str:
    """获取当前日期和时间。"""
    now = datetime.datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S（%A）")


# ---------------------------------------------------------------------------
# 小工具：安全计算器
# ---------------------------------------------------------------------------
# 绝不使用 eval()！而是把表达式解析成语法树(ast)后，只允许白名单内的运算。
import operator as _op

_BINOPS = {
    ast.Add: _op.add,
    ast.Sub: _op.sub,
    ast.Mult: _op.mul,
    ast.Div: _op.truediv,
    ast.FloorDiv: _op.floordiv,
    ast.Mod: _op.mod,
    ast.Pow: _op.pow,
}
_UNARYOPS = {ast.UAdd: _op.pos, ast.USub: _op.neg}
_FUNCS = {
    "abs": abs, "round": round,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "sqrt": math.sqrt, "log": math.log, "exp": math.exp,
    "floor": math.floor, "ceil": math.ceil, "pi": lambda: math.pi, "e": lambda: math.e,
}
_CONSTS = {"pi": math.pi, "e": math.e}


def _eval_node(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"不支持的常量: {node.value!r}")
    if isinstance(node, ast.BinOp):
        return _BINOPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        return _UNARYOPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError("只支持数学函数，如 sin/cos/sqrt/log/round/abs")
        args = [_eval_node(a) for a in node.args]
        return _FUNCS[node.func.id](*args)
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ValueError(f"不允许的变量: {node.id}")
    raise ValueError(f"不支持的语法: {type(node).__name__}")


def calculator(expression: str) -> str:
    """计算数学表达式，如 "12*34+56"、"sqrt(2)+1"、"3*(4+5)"。"""
    expr = expression.strip()
    # 允许少数中文运算词，便于大模型理解
    for zh, en in [("×", "*"), ("÷", "/"), ("乘以", "*"), ("除以", "/")]:
        expr = expr.replace(zh, en)
    try:
        tree = ast.parse(expr, mode="eval")
        result = _eval_node(tree.body)
    except Exception as e:  # 解析或计算失败 -> 返回错误说明，而不是崩溃
        return f"表达式无法计算: {e}"
    if isinstance(result, float) and result == int(result):
        result = int(result)  # 12.0 -> 12，更友好
    return f"{expression} = {result}"


# ---------------------------------------------------------------------------
# 小工具：天气（模拟数据！真正的天气工具需要调用天气 API）
# ---------------------------------------------------------------------------
def get_weather(city: str) -> str:
    """查询某城市的天气。注意：当前为教学演示，返回模拟数据，不真实联网。"""
    city = city.strip() or "北京"
    rng = random.Random(city)  # 同一城市每次结果一致
    temp = rng.randint(8, 33)
    condition = rng.choice(["晴", "多云", "阴", "小雨", "阵雨"])
    wind = rng.choice(["微风", "3-4 级", "5 级"])
    return f"{city}：{condition}，{temp}°C，{wind}（模拟数据，仅演示用）"


# ---------------------------------------------------------------------------
# 工具注册表：给主循环用的两份东西
#   1. TOOL_SCHEMAS —— 发给大模型的「说明书」（OpenAI function calling 格式）
#   2. TOOL_REGISTRY —— 名字 -> 真正执行的 Python 函数
# ---------------------------------------------------------------------------
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "获取当前的日期和时间（本地时间）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "计算数学表达式，支持 + - * / // % ** 与括号，以及 sin/cos/sqrt/log/round/abs、常数 pi/e。例子：'12*34+56'、'sqrt(2)+1'、'3*(4+5)'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "要计算的数学表达式"}
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memo",
            "description": "把一句话备忘保存到备忘本中（可用来记下用户的信息，之后随时回顾）。",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string", "description": "要保存的备忘内容"}},
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_memos",
            "description": "列出备忘本中保存的所有内容。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询某城市的天气情况。注意：返回的是模拟数据，仅用于教学演示。",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "城市名，如：北京"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索引擎检索（Bing/DuckDuckGo），输入中文关键词，返回若干条「标题+网址+摘要」，用于查资料。网络不通或解析失败时会如实报错。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "搜索关键词，中文即可，尽量具体"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": "抓取一个公网网页并提取正文纯文本（自动去标签），用于精读搜索结果里的高价值链接。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "以 http/https 开头的完整网址"}},
                "required": ["url"],
            },
        },
    },
]

TOOL_REGISTRY: dict[str, callable] = {
    "get_time": get_time,
    "calculator": calculator,
    "save_memo": save_memo,
    "list_memos": list_memos,
    "get_weather": get_weather,
    "web_search": web_search,
    "fetch_page": fetch_page,
}


def run_tool(name: str, args: dict) -> str:
    """主循环调用入口：按名字执行工具，任何异常都转成文本返回（而不是让循环崩溃）。"""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return f"错误：没有名为 {name} 的工具。可用工具：{', '.join(TOOL_REGISTRY)}"
    try:
        return str(fn(**args))
    except TypeError as e:
        return f"错误：调用 {name} 时参数不对：{e}"
    except Exception as e:
        return f"错误：工具 {name} 执行失败：{type(e).__name__}: {e}"
