# -*- coding: utf-8 -*-
"""工具层测试：计算器安全、run_tool 容错、抓取防护、文本清洗。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools


class TestCalculator(unittest.TestCase):
    def test_normal_expression(self):
        self.assertIn("464", tools.calculator("12*34 + 56"))

    def test_brackets_and_math_funcs(self):
        self.assertIn("37", tools.calculator("(15+3.5)*4/2"))
        r = tools.calculator("sqrt(2)+1")
        self.assertIn("sqrt(2)+1", r)

    def test_zh_operators(self):
        self.assertIn("= 12", tools.calculator("6 乘以 2"))

    def test_unsafe_code_rejected(self):
        for evil in ('__import__("os").system("x")', "2 +", "open('/etc/passwd')"):
            self.assertIn("无法计算", tools.calculator(evil), evil)


class TestRunTool(unittest.TestCase):
    def test_unknown_tool(self):
        self.assertIn("没有名为", tools.run_tool("no_such_tool", {}))

    def test_wrong_args(self):
        self.assertIn("参数不对", tools.run_tool("calculator", {}))

    def test_tool_exception_as_text(self):
        # get_weather 需要 city；异常应转文本而不崩溃
        self.assertIsInstance(tools.run_tool("get_weather", {}), str)


class TestFetchGuards(unittest.TestCase):
    def test_reject_localhost_and_private(self):
        for url in ("http://127.0.0.1:3080/x", "http://localhost/a",
                    "http://192.168.1.1/a", "http://10.0.0.1/a"):
            self.assertIn("安全", tools.fetch_page(url), url)

    def test_reject_non_http(self):
        self.assertIn("只支持 http/https", tools.fetch_page("ftp://x.com/a"))


class TestTextHelpers(unittest.TestCase):
    def test_strip_html(self):
        self.assertEqual("a b", tools._strip_html("<p>a<b>b</b></p>"))

    def test_plain_results_format(self):
        text = tools._plain_results([("标题", "https://x.com", "摘要")], 4000)
        self.assertTrue(text.startswith("1. 标题"))
        self.assertIn("https://x.com", text)


if __name__ == "__main__":
    unittest.main()
