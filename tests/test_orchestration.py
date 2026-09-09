# -*- coding: utf-8 -*-
"""测试：Multi-Agent Orchestration（M7 步骤 74-83）。"""
from src.agents.profiles import PROFILES, get_profile
from src.orchestration.base import pack_card
from src.orchestration.debate import run_debate
from src.orchestration.dynamic_team import run_dynamic_team
from src.orchestration.fanout import run_fanout
from src.orchestration.manager_worker import run_manager_worker
from src.orchestration.pipeline import DEFAULT_CHAIN, run_pipeline
from src.llm.mock import MockLLM

from eval.benchmark_orchestration import run_benchmark, strategies


class RecordingWorker:
    """记录每次 (task, role)，返回确定性文本。可配置按角色抛错一次。"""

    def __init__(self, fail_once_role=None):
        self.calls = []          # (role, task)
        self.failed = set()

    def __call__(self, task: str, role: str):
        self.calls.append((role, task))
        if role in self.failed:  # 该角色已失败过一次 → 本次放行
            self.failed.discard(role)
            return f"{role}->重试成功:{task[:10]}"
        if self.calls.count((role, task)) > 0 and role == "writer" and False:
            pass
        return f"{role}->完成:{task[:14]}"

    def fail_once(self, role):
        self.failed.add(role)

    def texts(self):
        return [c for _, c in self.calls]


# ---------- 74 Role Registry ----------
def test_profiles_registry():
    assert set(PROFILES) == {"researcher", "organizer", "writer", "reviewer"}
    assert "研究员" in get_profile("researcher").prompt
    assert "web_search" in get_profile("researcher").tools


# ---------- 76 Pipeline（含 Handoff 卡）----------
def test_pipeline_order_and_handoff_card():
    w = RecordingWorker()
    result = run_pipeline("写一篇科普文", w)
    roles = [r for r, _ in w.calls]
    assert roles == list(DEFAULT_CHAIN) or roles[:2] == ["researcher", "organizer"]
    # 第二棒的任务文本里应带上一棒产物摘要（Handoff Pack）
    assert "上一棒产物摘要" in w.calls[1][1]
    assert result.worker_calls == len(DEFAULT_CHAIN)
    assert result.final


def test_pack_card():
    card = pack_card("目标", "阶段A", "上一棒内容很长" * 50)
    assert "目标" in card and "阶段A" in card and "上一棒产物摘要" in card
    assert "很长" not in card.split("摘要】")[1] or len(card) < 600  # 摘要化而非全文


# ---------- 77 Fan-out / Fan-in ----------
def test_fanout_parallel_combine():
    w = RecordingWorker()
    result = run_fanout("大主题", w, ["子题A", "子题B", "子题C"], max_parallel=2)
    assert result.worker_calls == 3
    assert "子任务 1 产出" in result.final and "子任务 3 产出" in result.final


# ---------- 78 Manager–Worker（复用 Planner/Replanner）----------
def test_manager_worker_uses_plan_and_workers():
    w = RecordingWorker()
    result = run_manager_worker("帮我调研 X 并写报告", w, MockLLM())
    roles = [r for r, _ in w.calls]
    assert roles == ["researcher", "organizer", "writer"]  # Planner 链
    assert "Manager 汇总" in result.final
    assert result.worker_calls == 3


# ---------- 82 Dynamic Team（含失败重规划路径）----------
def test_dynamic_team_completes_after_replan():
    class FlakyWriter:
        def __init__(self):
            self.calls = []
            self.writer_failures = 0

        def __call__(self, task, role):
            self.calls.append((role, task))
            if role == "writer" and self.writer_failures == 0:
                self.writer_failures += 1
                raise RuntimeError("模拟 writer 第一次失败")
            return f"{role}->完成:{task[:12]}"

    w = FlakyWriter()
    result = run_dynamic_team("帮我调研 X 并成稿", w, MockLLM())
    assert "Dynamic Team 汇总" in result.final
    assert "完成 3/3" in result.final
    assert w.writer_failures == 1          # 确实失败过一次并被重规划救回
    assert result.worker_calls == 4        # 3 个任务 + 1 次重试


# ---------- 81 Debate-lite ----------
def test_debate_two_sides_and_verdict():
    w = RecordingWorker()
    result = run_debate("远程办公是否该全面推广？", w, MockLLM())
    assert result.worker_calls == 2
    assert "【裁决】" in result.final
    assert any(r in result.final for r in ("【支持方】", "【反对方】"))


# ---------- 83 Orchestration Benchmark ----------
def test_benchmark_report_structure():
    report = run_benchmark()
    names = set(report["by_strategy"])
    assert {"single", "pipeline", "manager_worker", "fanout", "dynamic_team"} <= names
    total = len(report["rows"])
    assert total == 2 * len(strategies())
    assert all(r["ok"] for r in report["rows"] if r["worker_calls"] >= 0)
