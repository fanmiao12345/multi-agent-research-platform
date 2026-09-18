# -*- coding: utf-8 -*-
"""React 前端页浏览器级验收（BROWSER_REGRESSION.md 引入步骤落地）。

依赖：playwright（dev 依赖）+ chromium（`python -m playwright install chromium`）；
未安装时整文件 skip，不影响离线回归。服务进程在测试内自管（uvicorn 后台线程），
覆盖两个旅程：
1. 正常旅程：页面渲染 → 提交任务 → 轮询到终态 → 截图留证；
2. 回退旅程：vendor 全部不可达时显示明确回退提示（不白屏、不报堆栈）。
"""
import re
import socket
import threading
import time
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api",
                                      reason="playwright 未安装（dev 依赖）")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCREENSHOT_DIR = REPO_ROOT / "eval" / "reports" / "react_browser"


@pytest.fixture(scope="module")
def base_url():
    import uvicorn
    from src.interfaces.web.fastapi_app import build_app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(build_app(), host="127.0.0.1", port=port,
                            log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _fresh_page(browser, console_errors):
    context = browser.new_context()
    page = context.new_page()
    page.on("console", lambda msg: console_errors.append(msg.text)
            if msg.type == "error" else None)
    return context, page


def test_react_page_full_journey(base_url):
    """正常旅程：渲染 → 提交 mock 任务 → 轮询到终态 → 截图。"""
    console_errors = []
    with playwright_sync.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context, page = _fresh_page(browser, console_errors)
            page.goto(base_url)
            page.wait_for_load_state("networkidle")

            assert "研究任务工作台" in page.locator("h1").inner_text()
            textarea = page.locator("textarea")
            assert textarea.is_visible()
            assert "加载失败" not in page.locator("#root").inner_text()

            textarea.click()
            # 逐键输入模拟真实用户：fill() 一次性灌值与受控组件的 onInput 合不来
            textarea.press_sequentially("计算 27*43，并说明计算过程", delay=10)
            page.get_by_role("button", name=re.compile("提交任务")).click()

            # Mock 大脑下任务秒级完成；等状态卡出现且离开 running
            page.wait_for_selector(".card", timeout=30000)
            deadline = time.time() + 30
            status_text = "running"
            while time.time() < deadline:
                status_text = page.locator(".card .status").inner_text().strip()
                if status_text != "running":
                    break
                page.wait_for_timeout(300)
            assert status_text in ("completed", "partial", "cancelled", "failed")
            assert page.locator(".card pre").count() >= 1

            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SCREENSHOT_DIR / "react_page_final.png"),
                            full_page=True)

            # 框架自身错误才算失败；favicon 404 之类资源噪音除外
            real_errors = [e for e in console_errors if "favicon" not in e]
            assert real_errors == [], f"控制台报错：{real_errors[:3]}"
            context.close()
        finally:
            browser.close()


def test_react_page_offline_fallback_message(base_url):
    """回退旅程：vendor 与 CDN 都不可达时显示明确提示，不白屏。"""
    console_errors = []
    with playwright_sync.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context, page = _fresh_page(browser, console_errors)
            page.route(re.compile(r".*(vendor|unpkg).*"),
                       lambda route: route.abort())
            page.goto(base_url)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(800)          # 给回退逻辑执行时间
            text = page.locator("#root").inner_text()
            assert "React 运行时加载失败" in text
            assert "workbench" in text          # 指向零依赖默认入口
            context.close()
        finally:
            browser.close()
