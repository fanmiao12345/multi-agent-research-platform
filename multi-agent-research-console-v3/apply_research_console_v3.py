#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply Research Console V3 to the current inline Workbench UI.

Only replaces INDEX_HTML; Python HTTP/API/backend logic is untouched.
"""
from __future__ import annotations
import re, shutil, sys
from pathlib import Path

TARGET = Path("src/interfaces/web/workbench.py")
HTML = Path(__file__).with_name("research_console_v3.html")
BACKUP = TARGET.with_suffix(".py.before-research-console-v3.bak")

def die(msg: str):
    print("[research-console-v3] ERROR:", msg, file=sys.stderr)
    raise SystemExit(2)

def main():
    if not TARGET.exists():
        die("请在 multi-agent-research-platform 仓库根目录执行。")
    if not HTML.exists():
        die("缺少 research_console_v3.html")
    src = TARGET.read_text(encoding="utf-8")
    page = HTML.read_text(encoding="utf-8").strip()
    if '"""' in page:
        die('HTML 内含三引号，无法安全内嵌。')

    pattern = re.compile(
        r'INDEX_HTML\s*=\s*(?:r)?"""[\s\S]*?"""\s*\n\s*\n\s*def make_server',
        re.M,
    )
    matches = list(pattern.finditer(src))
    if len(matches) != 1:
        die(f"预期找到1个INDEX_HTML，实际{len(matches)}个；请确认workbench.py版本。")

    replacement = 'INDEX_HTML = r"""' + page + '"""\n\n\ndef make_server'
    # 必须使用函数替换，避免HTML/JS里的 \s、\r、\n 被 re.sub 当成替换模板转义。
    updated = pattern.sub(lambda _m: replacement, src, count=1)

    markers = [
        '<input id="task"', 'Agent Workbench', '资料与产物',
        'id="filepaths"', 'id="pastetext"', 'id="urls"',
        'id="flow"', 'id="jobtbl"', 'id="reportview"', 'id="btnexport"',
        'id="requireSections"', 'id="forbidClaims"', 'id="keyFacts"',
        'loadJobs', 'loadLibrary', 'renderReportMarkdown',
        'required_sections:lines(', 'split(/\\r?\\n/)',
    ]
    missing = [m for m in markers if m not in updated]
    if missing:
        die("兼容标记缺失：" + ", ".join(missing))

    if not BACKUP.exists():
        shutil.copy2(TARGET, BACKUP)
    TARGET.write_text(updated, encoding="utf-8")
    print("[research-console-v3] 完成")
    print("修改:", TARGET)
    print("备份:", BACKUP)
    print()
    print("建议验证：")
    print("  python -m py_compile src/interfaces/web/workbench.py")
    print("  python -m pytest tests/test_workbench.py tests/test_workbench_b3.py tests/test_workbench_s5.py tests/test_d9_integration.py -q")
    print("  python -m src.interfaces.web.workbench --port 8765")
    print("  浏览器打开 http://127.0.0.1:8765/")

if __name__ == "__main__":
    main()
