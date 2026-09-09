# -*- coding: utf-8 -*-
"""测试：Milestone 4（45-54）——风险/权限策略、动态选择、结果压缩、Skill、Subagent。"""
from src.harness.control.policy import allowed, requires_approval, select_tools
from src.harness.skills.recall import recall, tokenize
from src.harness.skills.registry import SkillRegistry
from src.harness.skills.router import route
from src.harness.tools.registry import (RISK_HIGH, RISK_LOW, RISK_MEDIUM,
                                        ToolRegistry, ToolSpec)
from src.harness.tools.result_processor import process
from src.harness.tools.subagent import ALLOWED_ROLES, register_subagent_tool
from src.llm.mock import MockLLM

from eval.evaluators.skill_metrics import evaluate as skill_evaluate


# ---------- 45/46 Risk & Permission Policy ----------
def test_policy_risk_and_approval():
    reg = ToolRegistry()
    reg.register(ToolSpec(name="read_a", description="r", func=lambda: "x",
                          risk_level=RISK_LOW))
    reg.register(ToolSpec(name="del_b", description="d", func=lambda: "x",
                          risk_level=RISK_HIGH, requires_approval=True))
    assert requires_approval(reg.get("del_b")) is True
    ok, _ = allowed(reg.get("read_a"), permissions=frozenset({"read_a"}))
    assert ok
    ok, reason = allowed(reg.get("del_b"), permissions=frozenset({"del_b"}), approver=None)
    assert not ok and "审批" in reason
    ok, _ = allowed(reg.get("del_b"), permissions=frozenset({"del_b"}), approver="boss")
    assert ok


# ---------- 47 Dynamic Tool Selection ----------
def test_select_tools_by_role_and_risk():
    reg = ToolRegistry.with_builtins()
    picked = select_tools(reg, role="calculator", risk_max=RISK_MEDIUM)
    assert [t.name for t in picked] == ["calculator"]
    low = select_tools(reg, risk_max=RISK_LOW, allowlist=["calculator", "current_time"])
    assert {t.name for t in low} == {"calculator", "current_time"}
    deny = select_tools(reg, deny=("calculator",))
    assert all(t.name != "calculator" for t in deny)


# ---------- 48 Result Processor ----------
def test_result_processor_truncates_and_dedupes():
    out = process("t", "行1\n行1\n行2" + "长" * 5000, max_chars=200)
    head_part = out.split("…（结果过长")[0]
    assert "行1" in head_part
    assert "行1\n行1" not in head_part   # 连续重复行已去重
    assert "已截断" in out
    assert len(out) < 300


# ---------- 49-52 Skill Registry / Recall / Router ----------
def test_skill_registry_loads_and_validates():
    reg = SkillRegistry()
    diag = reg.diagnostics()
    names = {s.name for s in reg.list()}
    assert {"quick-math", "deep-dive"} <= names
    assert diag["issues"] == {}  # 当前两个技能 schema 合法


def test_skill_recall_ranking():
    reg = SkillRegistry()
    r = recall(reg, "帮我计算 27*43 等于多少", top_k=3)
    assert r and r[0]["name"] == "quick-math"
    assert recall(reg, "现在几点了？") == []  # 无命中


def test_skill_router_mock_and_inject():
    reg = SkillRegistry()
    d = route(reg, MockLLM(), "帮我算一下 6*7")
    assert d["skill"] == "quick-math" and d["candidates"]
    none_d = route(reg, MockLLM(), "早上好")
    assert none_d["skill"] is None
    text = __import__("src.harness.skills.router", fromlist=["inject"]).inject(reg.get("quick-math"))
    assert "calculator" in text


# ---------- 53 Skill Eval ----------
def test_skill_eval_mock_all_correct():
    reg = SkillRegistry()
    report = skill_evaluate(reg, MockLLM())
    assert report["overall"]["accuracy"] == 1.0
    assert report["positive"]["accuracy"] == 1.0
    assert report["negative"]["accuracy"] == 1.0


# ---------- 54 Subagent as Tool ----------
def test_subagent_tool_delegates_and_returns_text():
    from src.harness.runtime.agent_runtime import AgentRuntime
    from src.harness.runtime.run_context import RuntimeContext

    runtime = AgentRuntime(MockLLM())
    reg = ToolRegistry.with_builtins()
    register_subagent_tool(reg, runtime)
    spec = reg.get("delegate_subagent")
    assert spec is not None and spec.risk_level == RISK_MEDIUM

    import tempfile
    out = tempfile.mkdtemp(prefix="_m4_", dir="workspaces")
    try:
        result = spec.func(role="agent", task="帮我计算 6*7", budget=3)
        assert "42" in result
    finally:
        import shutil
        shutil.rmtree(out, ignore_errors=True)


def test_subagent_rejects_unknown_role():
    from src.harness.runtime.agent_runtime import AgentRuntime
    reg = ToolRegistry.with_builtins()
    register_subagent_tool(reg, AgentRuntime(MockLLM()))
    out = reg.get("delegate_subagent").func(role="hacker", task="x")
    assert "不支持的子智能体角色" in out
