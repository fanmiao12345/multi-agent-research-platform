# -*- coding: utf-8 -*-
"""技能路由测试：目录解析、BM25 区分度、avoid_when 防误选。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent
from agent import _skill_catalog, _retrieve_candidates
from llm import MockLLM


class TestCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = _skill_catalog()
        cls.by_name = {c["name"]: c for c in cls.cat}

    def test_five_builtin_skills(self):
        self.assertEqual(
            sorted(self.by_name),
            ["article-writer", "deep-research", "material-organizer",
             "review-editor", "writing-outline"])

    def test_metadata_parsed(self):
        for name in ("article-writer", "deep-research", "material-organizer",
                     "review-editor", "writing-outline"):
            c = self.by_name[name]
            self.assertTrue(c["description"], name)
            self.assertGreaterEqual(len(c["intents"]), 2, name)
            self.assertGreaterEqual(len(c["avoid_when"]), 2, name)


class TestDistinctness(unittest.TestCase):
    """7 个典型问题必须路由到正确的技能（模拟大脑=检索 top1）。"""

    def test_top1_routing(self):
        cases = {
            "我想写一篇关于晨跑的文章，帮我梳理思路": "writing-outline",
            "帮我调研一下深海采矿的现状和争议": "deep-research",
            "把这些零散资料整理成写作素材包": "material-organizer",
            "根据素材包帮我写一篇完整的公众号文章": "article-writer",
            "帮我审一遍这篇稿子，事实和数据靠谱吗": "review-editor",
            "这篇稿子要润色一下再发": "review-editor",
            "现在几点了？": None,
        }
        cat = _skill_catalog()
        for question, expect in cases.items():
            cands = _retrieve_candidates(cat, question)
            top = cands[0]["name"] if cands else None
            self.assertEqual(top, expect, f"问题「{question}」路由错误")

    def test_avoid_when_penalty(self):
        # 「润色」命中 writing-outline 的 avoid_when，不应把它排进来
        cat = _skill_catalog()
        cands = _retrieve_candidates(cat, "帮我把这段文字润色一下")
        tops = [c["name"] for c in cands]
        self.assertNotIn("writing-outline", tops)

    def test_mock_route_no_crash(self):
        llm = MockLLM()
        self.assertEqual(agent._route_skill(llm, "现在几点了？", _skill_catalog()), None)
        # 「出大纲」是 writing-outline 的强信号（article-writer 的职责是已成稿）
        self.assertEqual(
            agent._route_skill(llm, "我想写篇文章，帮我出个大纲", _skill_catalog()),
            "writing-outline")


if __name__ == "__main__":
    unittest.main()
