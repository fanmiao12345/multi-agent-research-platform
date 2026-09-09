# -*- coding: utf-8 -*-
"""主智能体测试：Mock 离线路由 + stub 校验主控节奏/Fan-out/Handoff。"""
import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import master
import tools
from llm import MockLLM
from tests.common import fake_web_tools


class TestMasterMock(unittest.TestCase):
    def test_simple_task_single(self):
        with fake_web_tools():
            ans = master.run_master(MockLLM(), "现在几点了？")
        self.assertTrue(ans.strip())

    def test_complex_task_multi(self):
        with fake_web_tools():
            ans = master.run_master(
                MockLLM(), "帮我调研一下远程办公的利弊，写一篇公众号文章",
                {"kind": "公众号推文"})
        self.assertTrue(ans.strip())

    def test_force_single(self):
        with fake_web_tools():
            ans = master.run_master(MockLLM(), "帮我调研一下深海采矿", force_mode="single")
        self.assertTrue(ans.strip())


class BrainStub:
    """真实分支 stub：评估/拆题/验收/工人 四类调用各自应答。"""

    def __init__(self, topics_line):
        self.topics_line = topics_line  # 'TOPICS: 无需拆分' 或 'TOPICS: A、B'
        self.worker_roles = []
        self.reviews = 0
        self.split_calls = 0
        self.organizer_handoff = []

    def chat(self, messages, tools=None):
        system = messages[0]["content"] if messages else ""
        if "两种打法" in system:
            return {"content": "MODE: multi\nREASON: stub\n"
                               "STEPS: researcher,organizer,writer,editor",
                    "tool_calls": None}
        if "TOPICS" in system:
            self.split_calls += 1
            return {"content": self.topics_line, "tool_calls": None}
        if "请验收" in system:
            self.reviews += 1
            verdict = ("final" if self.worker_roles
                       and self.worker_roles[-1] == "editor" else "pass")
            return {"content": f"VERDICT: {verdict}\nFEEDBACK: 无\nNEXT: ",
                    "tool_calls": None}
        role = ("researcher" if "研究员 Agent" in system else
                "organizer" if "整理师 Agent" in system else
                "writer" if "撰稿人 Agent" in system else
                "editor" if "审校 Agent" in system else "?")
        self.worker_roles.append(role)
        user = messages[1]["content"] if len(messages) > 1 else ""
        if role == "organizer":
            self.organizer_handoff.append(user.startswith("【交接卡】"))
        return {"content": f"（{role} 的产物）", "tool_calls": None}


class TestMasterOrchestration(unittest.TestCase):
    def test_sequence_without_fanout(self):
        stub = BrainStub("TOPICS: 无需拆分")
        with fake_web_tools():
            ans = master.run_master(stub, "写一份研究报告", {"kind": "研究报告"})
        self.assertEqual(stub.worker_roles,
                         ["researcher", "organizer", "writer", "editor"])
        self.assertEqual(stub.reviews, 4)
        self.assertTrue(stub.organizer_handoff and stub.organizer_handoff[0])
        self.assertIn("（writer 的产物）", ans)

    def test_fanout_parallel_researchers(self):
        stub = BrainStub("TOPICS: 子题甲、子题乙")
        with fake_web_tools():
            ans = master.run_master(stub, "帮我写一份关于深海采矿的研究报告",
                                    {"kind": "研究报告"})
        cnt = Counter(stub.worker_roles)
        self.assertEqual(cnt["researcher"], 2)   # 并行两个研究员
        self.assertEqual(stub.split_calls, 1)
        self.assertEqual(stub.reviews, 4)
        self.assertEqual(cnt["organizer"], 1)
        self.assertEqual(cnt["writer"], 1)
        self.assertEqual(cnt["editor"], 1)
        self.assertTrue(stub.organizer_handoff and stub.organizer_handoff[0])
        self.assertIn("（writer 的产物）", ans)


if __name__ == "__main__":
    unittest.main()
