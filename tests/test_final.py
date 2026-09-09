# -*- coding: utf-8 -*-
"""测试：M12 最终实验生成器与交付文档。"""
import json
import os
import shutil
import uuid

import pytest

from eval.final_report import gather_metrics, render_final_report, render_tech_report

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def tmp_ws():
    d = os.path.join(ROOT, "workspaces", "_t_final_" + uuid.uuid4().hex[:6])
    os.makedirs(d, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_gather_metrics_offline(tmp_ws):
    m = gather_metrics(tmp_ws)
    # 三大 benchmark + skill eval 全离线跑通
    assert m["agent"]["totals"]["executed"] >= 1
    assert len(m["orchestration"]["by_strategy"]) >= 5
    assert m["skill"]["overall"]["accuracy"] is not None
    assert len(m["model_strategies"]) == 3


def test_reports_render(tmp_ws):
    m = gather_metrics(tmp_ws)
    final = render_final_report(m)
    assert "最终实验报告" in final
    assert "Ablation 问答" in final
    assert "No Recovery vs Durable Execution" in final
    tech = render_tech_report(m)
    assert "技术报告" in tech and "M11" in tech


def test_deliverables_exist():
    # 交付文档落位（由 generate() 在验证阶段产出，测试只校验存在性）
    for path in ("README.md", "docs/architecture.md",
                 "DEV_PLAN_LangGraph_Harness_From_Scratch.md"):
        assert os.path.exists(os.path.join(ROOT, path)), path
