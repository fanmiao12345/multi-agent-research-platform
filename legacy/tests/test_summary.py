# -*- coding: utf-8 -*-
"""A3 测试：stats 累计（token/调用/轮数）、quiet 模式、摘要行。"""
import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent
import master
from llm import MockLLM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class UsageStub:
    """有 usage 的大脑：第 1 次调工具，第 2 次给正文。"""

    def __init__(self):
        self.n = 0

    def chat(self, messages, tools=None):
        self.n += 1
        if self.n == 1:
            return {"content": None,
                    "tool_calls": [{"id": "u1", "name": "no_such_tool",
                                    "arguments": {"x": 1}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        return {"content": "done", "tool_calls": None,
                "usage": {"prompt_tokens": 200, "completion_tokens": 60}}


class TestStats(unittest.TestCase):
    def test_stats_accumulate(self):
        st = agent._new_stats()
        agent.run_agent(UsageStub(), "q", auto_skills=False, stats=st)
        self.assertEqual(st["llm_calls"], 2)          # 1 轮工具 + 1 轮回答
        self.assertEqual(st["tool_calls"], 1)
        self.assertEqual(st["rounds"], 2)
        self.assertTrue(st["tokens_known"])
        self.assertEqual(st["prompt_tokens"], 300)
        self.assertEqual(st["completion_tokens"], 110)
        self.assertIsInstance(st["elapsed"], float)

    def test_summary_line_unknown_tokens_for_mock(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            agent.print_summary("测试", agent._new_stats())
        self.assertIn("[运行摘要]", buf.getvalue())
        self.assertIn("未知", buf.getvalue())

    def test_master_single_prints_summary(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            master.run_master(MockLLM(), "现在几点了？")
        self.assertIn("[运行摘要]", buf.getvalue())


class TestQuiet(unittest.TestCase):
    def _run(self, extra_args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        return subprocess.run(
            [sys.executable, "agent.py", "--force-mock", *extra_args],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env,
            timeout=60).stdout

    def test_normal_has_details_and_summary(self):
        out = self._run(["-q", "现在几点了？"])
        self.assertIn("[第1轮]", out)
        self.assertIn("[运行摘要]", out)

    def test_quiet_hides_details_keeps_summary(self):
        out = self._run(["--quiet", "-q", "现在几点了？"])
        self.assertNotIn("[第1轮]", out)
        self.assertIn("[运行摘要]", out)
        self.assertIn("小智", out)


if __name__ == "__main__":
    unittest.main()
