#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replace only the Workbench inline INDEX_HTML with Research Console V2."""
from __future__ import annotations
import re, shutil, sys
from pathlib import Path

TARGET = Path("src/interfaces/web/workbench.py")
HTML_FILE = Path(__file__).with_name("research_console_v2.html")
BACKUP = TARGET.with_suffix(".py.before-research-console-v2.bak")

def die(msg: str):
    print("[research-console-v2] ERROR:", msg, file=sys.stderr)
    raise SystemExit(2)

def main():
    if not TARGET.exists():
        die("请把本脚本放到 multi-agent-research-platform 仓库根目录执行。")
    if not HTML_FILE.exists():
        die(f"缺少 {HTML_FILE.name}")
    src = TARGET.read_text(encoding="utf-8")
    html = HTML_FILE.read_text(encoding="utf-8").strip()
    if '"""' in html:
        die('HTML 中出现三引号，无法安全内嵌。')

    pattern = re.compile(
        r'INDEX_HTML\s*=\s*(?:r)?"""[\s\S]*?"""\s*\n\s*\n\s*def make_server',
        re.M,
    )
    matches = list(pattern.finditer(src))
    if len(matches) != 1:
        die(f"预期找到 1 个 INDEX_HTML 区块，实际找到 {len(matches)} 个。仓库版本可能已变化。")

    replacement = 'INDEX_HTML = r"""' + html + '"""\n\n\ndef make_server'
    updated = pattern.sub(lambda _m: replacement, src, count=1)

    required = [
        '<input id="task"', 'Agent Workbench', 'id="jobtbl"', 'id="trace"',
        'async function exportReport()', 'renderReportMarkdown(await textFetch',
        'id="btncancel"', 'id="eval"',
    ]
    missing = [x for x in required if x not in updated]
    if missing:
        die("生成结果缺少关键兼容标记：" + ", ".join(missing))

    if not BACKUP.exists():
        shutil.copy2(TARGET, BACKUP)
    TARGET.write_text(updated, encoding="utf-8")

    print("[research-console-v2] 已完成 UI 替换")
    print("  修改:", TARGET)
    print("  备份:", BACKUP)
    print("建议执行：")
    print("  python -m py_compile src/interfaces/web/workbench.py")
    print("  python -m pytest tests/test_workbench.py tests/test_workbench_b3.py tests/test_workbench_s5.py -q")
    print("  python -m src.interfaces.web.workbench --port 8765")
    print("然后打开 http://127.0.0.1:8765/")

if __name__ == "__main__":
    main()
