# -*- coding: utf-8 -*-
"""角色注册表与共享工作区测试。"""
import os
import shutil
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import roles as roles_mod
import workspace


class TestRoles(unittest.TestCase):
    def test_registry_lists_builtin(self):
        self.assertEqual(roles_mod.list_roles(),
                         ["editor", "organizer", "researcher", "writer"])

    def test_researcher_meta(self):
        r = roles_mod.load_role("researcher")
        self.assertTrue(r["use_tools"])
        self.assertEqual(r["budget"], "research")
        self.assertIn("研究员 Agent", r["system"])

    def test_writer_meta(self):
        w = roles_mod.load_role("writer")
        self.assertFalse(w["use_tools"])
        self.assertEqual(w["budget"], "default")

    def test_missing_role_returns_none(self):
        self.assertIsNone(roles_mod.load_role("no-such-role"))

    def test_role_system_raises_with_hint(self):
        with self.assertRaises(RuntimeError):
            roles_mod.role_system("no-such-role")


class TestWorkspace(unittest.TestCase):
    def setUp(self):
        # 临时目录放在 research_output 下、用普通 makedirs 创建
        # （本机文件沙箱对 tempfile.mkdtemp 创建的目录限制写入，需避开）
        out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "research_output")
        os.makedirs(out, exist_ok=True)
        self.dir = os.path.join(out, "_t_ws_" + uuid.uuid4().hex[:8])
        os.makedirs(self.dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_state_roundtrip(self):
        workspace.init_state(self.dir, "测试任务", {"kind": "x"}, "test")
        p1 = workspace.record_artifact(self.dir, "01_a.md", "内容甲",
                                       role="researcher", verdict="pass")
        self.assertTrue(os.path.exists(p1))
        p2 = workspace.set_final(self.dir, "final.md", "终稿")
        state = workspace.read_state(self.dir)
        self.assertEqual(state["task"], "测试任务")
        self.assertEqual(state["final"], "final.md")
        self.assertEqual(len(state["steps"]), 1)
        self.assertEqual(state["steps"][0]["role"], "researcher")
        self.assertEqual(state["steps"][0]["verdict"], "pass")
        self.assertTrue(os.path.exists(p2))

    def test_record_preview_limited(self):
        workspace.init_state(self.dir, "t", {}, "test")
        workspace.record_artifact(self.dir, "x.md", "长" * 500)
        state = workspace.read_state(self.dir)
        self.assertLessEqual(len(state["steps"][0]["preview"]), 200)


if __name__ == "__main__":
    unittest.main()
