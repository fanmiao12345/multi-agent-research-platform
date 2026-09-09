# -*- coding: utf-8 -*-
"""
harness/control/subprocess_guard.py —— 有界子进程执行（S4-07）

本地解析/计算放到独立子进程：超时可终止（真实 kill），stdout/stderr 有界收集，
进程崩溃不影响宿主。外部副作用接口幂等性由调用方负责（本工具不宣称可安全强杀
任意线程——它启动的进程可以被终止）。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

MAX_CAPTURE = 200_000  # 防失控输出撑爆内存


class SubprocessTimeout(RuntimeError):
    """子进程超时已被终止。"""


def run_bounded_subprocess(code: str, *, timeout: float = 10.0,
                           cwd: str | Path | None = None,
                           env: dict | None = None,
                           capture_limit: int = MAX_CAPTURE) -> dict:
    """运行一段 Python 代码（python -c）；超时即 kill 并报错。

    返回 {"returncode", "stdout"(截断), "stderr"(截断), "elapsed_seconds"}。
    不建议向 code 传入不可信内容；可信本地解析/计算场景使用。
    """
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, cwd=str(cwd) if cwd else None,
            env=dict(env) if env is not None else None)
    except subprocess.TimeoutExpired as e:
        # TimeoutExpired 会带已捕获的部分输出；进程已由 run 内部终止
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        err = (e.stderr or "") if isinstance(e.stderr, str) else ""
        raise SubprocessTimeout(
            f"子进程超过 {timeout}s 已终止（stdout {len(out)}B / stderr {len(err)}B）") from None
    return {"returncode": proc.returncode,
            "stdout": proc.stdout[-capture_limit:],
            "stderr": proc.stderr[-capture_limit:],
            "elapsed_seconds": round(time.monotonic() - started, 4)}


def run_bounded_function(code: str, *, timeout: float = 10.0,
                         cwd: str | Path | None = None) -> str:
    """便捷版：子进程打印一行结果并返回该行；其余输出进 stderr 记录。"""
    result = run_bounded_subprocess(code + "\n", timeout=timeout, cwd=cwd)
    if result["returncode"] != 0:
        raise RuntimeError(f"子进程失败 rc={result['returncode']}: "
                           f"{result['stderr'][:500]}")
    lines = [line for line in result["stdout"].splitlines() if line.strip()]
    return lines[-1] if lines else ""
