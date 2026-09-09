# -*- coding: utf-8 -*-
"""一键测试入口：python run_tests.py（Windows 也可用 run_tests.bat）。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(
        start_dir=os.path.join(ROOT, "tests"), pattern="test_*.py",
        top_level_dir=ROOT)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    # 清理测试运行产生的临时运行目录，保持仓库整洁
    import shutil
    out_dir = os.path.join(ROOT, "research_output")
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
    sys.exit(0 if result.wasSuccessful() else 1)
