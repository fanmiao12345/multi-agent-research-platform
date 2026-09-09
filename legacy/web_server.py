# -*- coding: utf-8 -*-
"""
web_server.py —— 把 agent-mvp 变成 Web 版（后端，零第三方依赖）

前端在 webui/（React + Vite 工程），build 产物放 webui/dist —— 本服务顺带托管它：
    python web_server.py            # 启动 http://127.0.0.1:8765（自动打开浏览器）
    python web_server.py --port 9000 --host 0.0.0.0
    python web_server.py --force-mock     # 强制离线模拟大脑（不想花 token 调试界面时）

开发期也可以只当 API 用：webui 里 npm run dev（Vite 5173 端口），/api 请求会被
代理到本服务。浏览器与后端的通信协议是「NDJSON 流」：POST /api/chat 返回
text/event-stream 式的一行一个 JSON 事件，把 ReAct 循环的每一步实时推给前端：

    {"t":"boot", ...}         本次会话使用的「大脑」信息（模型/接口/温度/Key 来源）
    {"t":"skill", ...}        技能路由命中，加载了某个技能
    {"t":"round", ...}        第 N 轮：大脑说的话 + 它请求调用的工具清单
    {"t":"tool", ...}         开始执行某个工具
    {"t":"tool_result", ...}  工具真实返回的结果
    {"t":"notice", ...}       过程提示（如预算用尽）
    {"t":"answer", ...}       最终答案
    {"t":"error", ...}        出错了（如 Key 无效）
    {"t":"done"}              本次任务结束

多轮记忆：每个会话（前端生成的 sid）在服务端保留一份对话历史，
下一问自动带上 —— 和 agent.py 连续对话模式的机制完全一样。

教学提示：本项目刻意不引入任何第三方库 —— 服务端只用 Python 标准库
(http.server / urllib)，看懂这个文件的同时，也就看懂了「HTTP 接口」本身。
"""

from __future__ import annotations

import argparse
import glob
import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent
import config as cfgmod
import tools

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, "webui", "dist")   # 前端 build 产物（npm run build）
CFG = cfgmod.load_config()                            # config.ini（改配置需重启本服务）

MAX_SESSIONS = 50         # 服务端最多同时记住多少个会话的历史（防内存无限涨）
MAX_QUESTION = 20000      # 单条提问最长字符数（防超长请求）
_OPENED_BROWSER = False   # 只自动开一次浏览器


# ---------------------------------------------------------------------------
# 会话（sid -> 对话状态）：这就是「记忆」在服务端的形态
#  -- messages: 完整对话历史（None = 还没聊过，run_agent 会创建）
#  -- running:  该会话是否正在跑任务（防同会话并发提问搞乱历史）
# ---------------------------------------------------------------------------
SESSIONS: dict[str, dict] = {}
_SESSIONS_LOCK = threading.Lock()


def _session(sid: str) -> dict:
    """取（或建）一个会话。会话数超上限时丢最久没用的那个（极简 LRU）。"""
    with _SESSIONS_LOCK:
        now = time.time()
        if sid not in SESSIONS:
            if len(SESSIONS) >= MAX_SESSIONS:
                oldest = min(SESSIONS, key=lambda k: SESSIONS[k]["touched"])
                SESSIONS.pop(oldest, None)
            SESSIONS[sid] = {"messages": None, "running": False, "touched": now}
        sess = SESSIONS[sid]
        sess["touched"] = now
        return sess


# ---------------------------------------------------------------------------
# 技能管理：技能 = skills/*.md「数据文件」，本服务只做文件层面的增删移动。
#   -- 启用（active）：   skills/<名字>.md
#   -- 停用（disabled）： skills/_disabled/<名字>.md   （文件仍在，重启保持）
# 技能热插拔的机制在 agent.py 里：每次提问都现扫磁盘 —— 所以这里的任何改动，
# 下一次提问立即生效，不需要重启后端，也完全不影响命令行 / multi.py。
# ---------------------------------------------------------------------------
DISABLED_DIR = os.path.join(agent.SKILLS_DIR, "_disabled")
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _skill_paths(name: str) -> tuple[str, str]:
    """返回 (启用路径, 停用路径)。名字已按正则校验，不会逃出 skills/ 目录。"""
    return (os.path.join(agent.SKILLS_DIR, f"{name}.md"),
            os.path.join(DISABLED_DIR, f"{name}.md"))


def _scan_skill_dir(enabled: bool) -> list[dict]:
    """扫一个技能目录（启用目录或 _disabled 目录），解析每个 .md 的元信息。"""
    folder = agent.SKILLS_DIR if enabled else DISABLED_DIR
    out = []
    for path in sorted(glob.glob(os.path.join(folder, "*.md"))):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        meta = agent._parse_frontmatter(lines)
        out.append({
            "name": name,
            "description": meta.get("description", ""),
            "keywords": meta.get("keywords", []),
            "intents": meta.get("intents", []),
            "avoid_when": meta.get("avoid_when", []),
            "enabled": enabled,
        })
    return out


def skills_list() -> list[dict]:
    """全部技能（启用的在前，停用的在后），供前端「技能库」面板展示。"""
    return _scan_skill_dir(True) + _scan_skill_dir(False)


def skill_import(body: dict) -> tuple[int, dict]:
    """导入技能：POST {name?, content}。name 缺省时从 frontmatter 的 name: 提取。"""
    name = str(body.get("name", "")).strip()
    content = str(body.get("content", ""))
    if not content.strip():
        return 400, {"error": "技能内容为空"}
    if not name:
        # 从 --- 元信息块里找 name:（前端一般会给全，这里兜底）
        for line in content.splitlines():
            m = re.match(r"^\s*name\s*:\s*([A-Za-z0-9_-]+)\s*$", line)
            if m:
                name = m.group(1)
                break
    if not name:
        return 400, {"error": "请填写技能名（或在 frontmatter 里写 name: xxx）"}
    if not SKILL_NAME_RE.match(name):
        return 400, {"error": "技能名只能由字母/数字/_/- 组成，且以字母或数字开头"}
    active, disabled = _skill_paths(name)
    if os.path.exists(active) or os.path.exists(disabled):
        return 409, {"error": f"技能「{name}」已存在（可先停用/删除旧的后再导入）"}
    try:
        with open(active, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as e:
        return 500, {"error": f"写入 skills/{name}.md 失败：{e}"}
    return 200, {"ok": True, "message": f"已导入技能「{name}」，下一次提问即可使用", "name": name}


def skill_toggle(body: dict) -> tuple[int, dict]:
    """停用/启用：POST {name, enable}。停用 = 移入 skills/_disabled/，不删文件。"""
    name = str(body.get("name", "")).strip()
    enable = bool(body.get("enable"))
    if not SKILL_NAME_RE.match(name):
        return 400, {"error": "技能名不合法"}
    active, disabled = _skill_paths(name)
    src = active if not enable else disabled
    dst = disabled if not enable else active
    if not os.path.exists(src):
        where = "启用列表" if enable else "停用列表"
        return 404, {"error": f"技能「{name}」不在{where}中"}
    try:
        if not enable:
            os.makedirs(DISABLED_DIR, exist_ok=True)
        os.replace(src, dst)
    except OSError as e:
        return 500, {"error": f"操作失败：{e}"}
    word = "停用" if not enable else "重新启用"
    return 200, {"ok": True, "message": f"已{word}技能「{name}」", "name": name}


def skill_delete(body: dict) -> tuple[int, dict]:
    """删除技能：POST {name}。永久删除 skills/ 下对应的 .md 文件（不可恢复）。"""
    name = str(body.get("name", "")).strip()
    if not SKILL_NAME_RE.match(name):
        return 400, {"error": "技能名不合法"}
    active, disabled = _skill_paths(name)
    for p in (active, disabled):
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError as e:
                return 500, {"error": f"删除 skills/{os.path.basename(p)} 失败：{e}"}
            return 200, {"ok": True, "message": f"已删除技能「{name}」", "name": name}
    return 404, {"error": f"找不到技能「{name}」"}


class Api:
    """本次进程的「大脑」与引擎信息 —— 启动时探测一次（同 CLI：无 Key 自动 Mock）。"""

    def __init__(self, args):
        ns = argparse.Namespace(force_mock=args.force_mock, api_key=args.api_key)
        self.llm, self.key_source = agent._pick_llm(ns)
        from llm import LLM
        self.mock = not isinstance(self.llm, LLM)
        self.forced_skill = args.skill
        try:
            self.system_prompt = agent._build_system_prompt(args.skill)
        except RuntimeError as e:  # 强制的技能不存在 -> 启动即报错退出（同 CLI）
            print(f"\n[错误] {e}")
            sys.exit(1)
        self.cfg = cfgmod.load_config()

    # ---- 给前端看的「引擎状态」（相当于 CLI 启动横幅的 JSON 版）----
    def state(self) -> dict:
        m = self.cfg["model"]
        return {
            "brain": "MockLLM" if self.mock else "LLM",
            "model": getattr(self.llm, "model", "deepseek-chat"),
            "base_url": getattr(self.llm, "base_url", ""),
            "temperature": getattr(self.llm, "temperature", 0.7),
            "key_source": self.key_source,
            "key_configured": self.key_source is not None,
            "max_rounds": int(self.cfg["agent"]["max_rounds"]),
            "research_max_rounds": int(self.cfg.get("research", {}).get("max_rounds", "20")),
            "forced_skill": self.forced_skill,
            "skills": [{"name": c["name"], "description": c["description"]}
                       for c in agent._skill_catalog()],
            "version": "agent-mvp web",
        }


# ---------------------------------------------------------------------------
# HTTP 处理器（ThreadingHTTPServer：一个请求一个线程，天然支持并发会话）
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # 1.0 = 响应结束即断开，天然支持「流式写完后关闭」
    server_version = "AgentMVPWeb/0.1"
    api: Api = None                # 类属性，启动时由 serve() 注入

    # ---- 小工具 ----
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(n) if n > 0 else b"{}"
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # ---- 路由 ----
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/state":
            self._json(200, self.api.state())
        elif parsed.path == "/api/skills":
            self._json(200, {"skills": skills_list()})
        else:
            self._serve_static(parsed.path)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/chat":
            self._handle_chat()
        elif parsed.path == "/api/reset":
            sid = str(self._read_body().get("sid", ""))
            with _SESSIONS_LOCK:
                SESSIONS.pop(sid, None)
            self._json(200, {"ok": True})
        elif parsed.path == "/api/skills":          # 导入技能：{name?, content}
            code, obj = skill_import(self._read_body())
            self._json(code, obj)
        elif parsed.path == "/api/skills/toggle":   # 停用/启用：{name, enable}
            code, obj = skill_toggle(self._read_body())
            self._json(code, obj)
        elif parsed.path == "/api/skills/delete":   # 删除技能：{name}
            code, obj = skill_delete(self._read_body())
            self._json(code, obj)
        else:
            self._json(404, {"error": "not found"})

    # ---- 核心：一次提问 = 一场 NDJSON 事件直播 ----
    def _handle_chat(self):
        body = self._read_body()
        sid = str(body.get("sid", ""))[:64]
        question = str(body.get("question", "")).strip()
        if not sid:
            self._json(400, {"error": "缺少 sid"})
            return
        if not question:
            self._json(400, {"error": "问题不能为空"})
            return
        question = question[:MAX_QUESTION]

        sess = _session(sid)
        if sess["running"]:
            self._json(409, {"error": "该会话正在运行中，请稍候"})
            return
        sess["running"] = True

        # 告诉浏览器「开始」+ 引擎信息；之后每行一个 JSON 事件，写完立即 flush
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()

        def emit(payload: dict):
            line = json.dumps({"t": payload.get("type"), **payload}, ensure_ascii=False)
            self.wfile.write((line + "\n").encode("utf-8"))
            self.wfile.flush()

        try:
            emit({"type": "boot", **self.api.state()})
            history_len = len(sess["messages"]) if sess["messages"] is not None else -1

            def run():
                # 把 emit 转成 agent.run_agent 认得的格式：删掉冗余的 type 键没意义，
                # 直接原样转发（run_agent 的载荷里本身就有 type）
                return agent.run_agent(
                    self.api.llm, question,
                    messages=sess["messages"],
                    system_prompt=self.api.system_prompt,
                    auto_skills=self.api.forced_skill is None,   # 强制技能时关闭自动路由
                    prefer_skill=None,
                    emit=emit)

            try:
                _, msgs = run()
                sess["messages"] = msgs   # 记住本轮历史：下一问「小智」才记得你
            except Exception as e:        # Key 无效 / 断网 / 超时…… 如实告诉前端
                # 回滚：把本轮追加进历史的半截内容切掉，会话保持可用
                if sess["messages"] is not None and history_len >= 0:
                    del sess["messages"][history_len:]
                else:
                    sess["messages"] = None
                emit({"type": "error", "message": str(e)})
            finally:
                sess["running"] = False
                emit({"type": "done"})
        except Exception as e:  # 连接已断等 —— 放弃本次直播，但不影响别的会话
            try:
                self._json(500, {"error": str(e)})
            except Exception:
                pass
            finally:
                sess["running"] = False

    # ---- 托管前端 build 产物（webui/dist）；没构建过就给个中文引导页 ----
    def _serve_static(self, path: str):
        if not os.path.isdir(DIST_DIR):
            body = ("<meta charset='utf-8'><title>agent-mvp web</title>"
                    "<body style='font-family:system-ui;padding:48px;line-height:1.8'>"
                    "<h2>后端已启动，但还没构建前端。</h2>"
                    "<p>开发模式（热更新）：</p><pre>cd webui &amp;&amp; npm install &amp;&amp; npm run dev</pre>"
                    "<p>然后浏览器打开 <a href='http://127.0.0.1:5173'>http://127.0.0.1:5173</a>；"
                    "或先构建再刷新本页：<code>cd webui &amp;&amp; npm run build</code></p>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("", "/"):
            path = "/index.html"
        rel = urllib.parse.unquote(path).lstrip("/")
        full = os.path.normpath(os.path.join(DIST_DIR, rel))
        if not full.startswith(os.path.normpath(DIST_DIR)) or not os.path.isfile(full):
            self.send_response(404)
            self.end_headers()
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # 精简控制台日志，一眼看清每次请求
        sys.stdout.write("[web] " + (fmt % args) + "\n")
        sys.stdout.flush()


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True,
          args=None):
    Handler.api = Api(args)
    srv = ThreadingHTTPServer((host, port), Handler)
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    print("=" * 60)
    print(" Agent MVP Web 已启动")
    print(f"  访问: {url}")
    print(f"  大脑: {'MockLLM（离线模拟）' if Handler.api.mock else 'LLM（真实模型）'}"
          f"   模型: {Handler.api.state()['model']}"
          f"   温度: {Handler.api.state()['temperature']}")
    print("  Key 来源: " + (Handler.api.key_source or "未配置（自动使用离线模拟大脑）"))
    if Handler.api.forced_skill:
        print(f"  技能: {Handler.api.forced_skill}（强制指定，关闭自动路由）")
    print("  提示: 改 config.ini（模型/Key/温度）后重启本服务生效；Ctrl+C 退出")
    print("=" * 60)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n再见！")
    finally:
        srv.server_close()


def main():
    parser = argparse.ArgumentParser(description="Agent MVP 的 Web 服务（零第三方依赖）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8765, help="端口（默认 8765）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--api-key", help="临时指定 DeepSeek API Key（也可填进 config.ini）")
    parser.add_argument("--force-mock", action="store_true", help="强制离线模拟大脑")
    parser.add_argument("--skill", metavar="名字", help="强制加载 skills/ 下的某个技能")
    args = parser.parse_args()
    serve(host=args.host, port=args.port, open_browser=not args.no_browser, args=args)


if __name__ == "__main__":
    main()
