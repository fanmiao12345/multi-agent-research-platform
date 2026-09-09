# -*- coding: utf-8 -*-
"""预算分级与触顶收尾测试（stub 大脑：永不主动结束，逼到预算用尽）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent
import multi
from multi import run_worker, RESEARCHER_SYSTEM


class NeverDoneStub:
    """有 tools 就永远请求（假工具，不联网）；无 tools（收尾）才给正文。"""

    def __init__(self):
        self.tool_rounds = 0
        self.wrap_calls = 0

    def chat(self, messages, tools=None):
        if tools:
            self.tool_rounds += 1
            return {"content": None, "tool_calls": [
                {"id": "s1", "name": "no_such_tool", "arguments": {"x": 1}}]}
        self.wrap_calls += 1
        return {"content": "收尾总结正文：只拿到了部分资料", "tool_calls": None}


class TestBudget(unittest.TestCase):
    def test_normal_task_uses_default_budget_and_wraps(self):
        s = NeverDoneStub()
        ans, _ = agent.run_agent(s, "随便问问", auto_skills=False)
        self.assertEqual(s.tool_rounds, agent.MAX_ROUNDS)
        self.assertTrue(ans.startswith("（预算用尽"))
        self.assertIn("收尾总结正文", ans)

    def test_research_task_gets_big_budget(self):
        s = NeverDoneStub()
        ans, _ = agent.run_agent(s, "调研一下深海采矿", auto_skills=False,
                                 prefer_skill="deep-research")
        self.assertEqual(s.tool_rounds, agent.RESEARCH_MAX_ROUNDS)
        self.assertIn("收尾总结正文", ans)

    def test_explicit_max_rounds_wins(self):
        s = NeverDoneStub()
        agent.run_agent(s, "q", auto_skills=False, max_rounds=3)
        self.assertEqual(s.tool_rounds, 3)

    def test_worker_wraps_on_budget_exhaustion(self):
        s = NeverDoneStub()
        final, _ = run_worker(s, RESEARCHER_SYSTEM, "研究主题：x", use_tools=True,
                              max_rounds=3)
        self.assertEqual(s.tool_rounds, 3)
        self.assertTrue(final.startswith("（预算用尽"))


if __name__ == "__main__":
    unittest.main()
