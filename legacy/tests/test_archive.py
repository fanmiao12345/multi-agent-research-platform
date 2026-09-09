# -*- coding: utf-8 -*-
"""A2 会话档案化测试：agent_runs 独立目录 + meta/transcript 内容。"""
import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import workspace

RUNS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "agent_runs")


class TestArchiveSession(unittest.TestCase):
    def _make(self, topic, marker):
        meta = {"mode": "one-shot", "llm": "MockLLM", "model": "",
                "key_source": None, "skill_mode": "auto",
                "question": marker}
        history = [{"role": "user", "content": marker},
                   {"role": "assistant", "content": "回答" + marker}]
        return workspace.archive_session(meta, history, topic=topic), history

    def test_two_archives_are_independent(self):
        d1, h1 = self._make("第一次问题", "alpha")
        d2, h2 = self._make("第二次问题", "beta")
        try:
            self.assertNotEqual(d1, d2)
            for d, h, marker in ((d1, h1, "alpha"), (d2, h2, "beta")):
                self.assertTrue(os.path.exists(os.path.join(d, "meta.json")))
                self.assertTrue(os.path.exists(os.path.join(d, "transcript.json")))
                with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
                    meta = json.load(f)
                self.assertEqual(meta["question"], marker)
                self.assertEqual(meta["messages"], 2)
                with open(os.path.join(d, "transcript.json"), encoding="utf-8") as f:
                    trans = json.load(f)
                self.assertEqual(trans[0]["content"], marker)
        finally:
            shutil.rmtree(d1, ignore_errors=True)
            shutil.rmtree(d2, ignore_errors=True)

    def test_empty_history_tolerated(self):
        # 空 history 也应能归档（messages=0），不崩溃
        d = workspace.archive_session({"mode": "chat"}, [], topic="空会话")
        try:
            self.assertTrue(os.path.isdir(d))
            self.assertEqual(sorted(os.listdir(d)),
                             ["meta.json", "transcript.json"])
            with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["messages"], 0)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
