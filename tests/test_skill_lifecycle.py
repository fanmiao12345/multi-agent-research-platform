# -*- coding: utf-8 -*-
"""Skill Harness：四态生命周期 / 依赖解析 / references 渐进加载 / 运行时接线。"""
import pytest

from src.harness.skills.lifecycle import (ACTIVATED, DEACTIVATED, DISCOVERED,
                                          RUNNING, SkillLifecycleError,
                                          SkillLifecycleManager, run_skill_task)
from src.harness.skills.registry import SkillRegistry
from src.harness.skills.router import inject
from src.harness.skills.types import LEVEL_FULLCONTENT, LEVEL_METADATA


def write_skill(root, stem, meta_lines, body="正文说明。"):
    lines = ["---"] + meta_lines + ["---", ""] + body.splitlines()
    path = root / f"{stem}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@pytest.fixture
def skill_dir(tmp_path):
    write_skill(tmp_path, "alpha", ["name: alpha", "description: 基础技能",
                                    "depends: beta"])
    write_skill(tmp_path, "beta", ["name: beta", "description: 依赖 gamma",
                                   "depends: gamma"])
    write_skill(tmp_path, "gamma", ["name: gamma", "description: 无依赖技能",
                                    "references: cheat_sheet"])
    ref_dir = tmp_path / "gamma"
    ref_dir.mkdir(exist_ok=True)
    (ref_dir / "cheat_sheet.md").write_text("# 速查表\n很长的参考资料……",
                                            encoding="utf-8")
    return tmp_path


@pytest.fixture
def lifecycle(skill_dir):
    return SkillLifecycleManager(SkillRegistry(skill_dir))


def test_scan_all_discovered(lifecycle):
    assert lifecycle.snapshot() == {"alpha": DISCOVERED, "beta": DISCOVERED,
                                    "gamma": DISCOVERED}


def test_invalid_transition_rejected(lifecycle):
    with pytest.raises(SkillLifecycleError):
        lifecycle.begin_run("gamma")               # discovered 直接 running = 非法
    with pytest.raises(SkillLifecycleError):
        lifecycle.deactivate("gamma")              # discovered → deactivated = 非法


def test_missing_dependency_rejected(lifecycle):
    write_skill(skill_dir := lifecycle.registry.root, "delta",
                ["name: delta", "description: 依赖不存在", "depends: ghost"])
    lifecycle.registry.reload()
    lifecycle.scan()
    with pytest.raises(SkillLifecycleError, match="依赖缺失.*ghost"):
        lifecycle.activate("delta")


def test_circular_dependency_rejected(tmp_path):
    write_skill(tmp_path, "p", ["name: p", "description: x", "depends: q"])
    write_skill(tmp_path, "q", ["name: q", "description: y", "depends: p"])
    manager = SkillLifecycleManager(SkillRegistry(tmp_path))
    with pytest.raises(SkillLifecycleError, match="循环依赖"):
        manager.activate("p")


def test_activate_resolves_dependency_chain_in_order(lifecycle):
    lifecycle.activate("alpha")                    # alpha→beta→gamma 拓扑序
    assert lifecycle.snapshot() == {"alpha": ACTIVATED, "beta": ACTIVATED,
                                    "gamma": ACTIVATED}


def test_full_lifecycle_flow(lifecycle):
    lifecycle.activate("gamma")
    assert lifecycle.state_of("gamma") == ACTIVATED
    lifecycle.begin_run("gamma")
    assert lifecycle.state_of("gamma") == RUNNING
    lifecycle.end_run("gamma")
    lifecycle.deactivate("gamma")
    assert lifecycle.state_of("gamma") == DEACTIVATED
    lifecycle.activate("gamma")                    # deactivated 可再次激活
    assert lifecycle.state_of("gamma") == ACTIVATED


def test_run_skill_task_returns_value_and_resets_state(lifecycle):
    lifecycle.activate("gamma")
    result = run_skill_task(lifecycle, "gamma", lambda: 42,
                            deactivate_after=False)
    assert result == 42 and lifecycle.state_of("gamma") == ACTIVATED

    def boom():
        raise ValueError("业务异常")
    with pytest.raises(ValueError):
        run_skill_task(lifecycle, "gamma", boom, deactivate_after=True)
    assert lifecycle.state_of("gamma") == DEACTIVATED   # 异常也不悬在 running


def test_lifecycle_publishes_bus_events(lifecycle):
    from src.orchestration.event_bus import EventBus
    bus = EventBus()
    lifecycle.event_bus = bus
    lifecycle.activate("gamma")
    events = bus.replay("skill_lifecycle")
    assert len(events) == 1
    assert events[0]["skill"] == "gamma"
    assert events[0]["to_state"] == ACTIVATED


# ---- references 渐进加载 ----

def test_load_reference_on_demand(lifecycle):
    content = lifecycle.registry.load_reference("gamma", "cheat_sheet")
    assert "速查表" in content
    with pytest.raises(KeyError):
        lifecycle.registry.load_reference("gamma", "不存在")     # 未声明
    with pytest.raises(FileNotFoundError):
        write_skill(lifecycle.registry.root, "lonely",
                    ["name: lonely", "description: 声明了但文件缺失",
                     "references: missing_ref"])
        lifecycle.registry.reload()
        lifecycle.scan()
        lifecycle.registry.load_reference("lonely", "missing_ref")


def test_inject_levels_metadata_vs_fullcontent(lifecycle):
    skill = lifecycle.registry.get("gamma")
    meta = inject(skill, level=LEVEL_METADATA)
    full = inject(skill, level=LEVEL_FULLCONTENT)
    assert "gamma" in meta and "正文说明" not in meta   # metadata 不带正文
    assert "正文说明" in full
    with pytest.raises(ValueError):
        inject(skill, level="references")               # references 不走 inject


def test_metadata_layer_costs_far_less_tokens(lifecycle):
    from src.harness.context.budget import estimate_tokens
    skill = lifecycle.registry.get("gamma")
    assert (estimate_tokens(inject(skill, level=LEVEL_METADATA)) <
            estimate_tokens(inject(skill, level=LEVEL_FULLCONTENT)))


# ---- AgentRuntime 接线：命中技能走生命周期，异常技能跳过 ----

def test_runtime_skill_lifecycle_roundtrip(tmp_path):
    from src.harness.runtime.agent_runtime import AgentRuntime
    from src.harness.runtime.run_context import RuntimeContext
    from src.llm.mock import MockLLM

    write_skill(tmp_path, "calc-help", [
        "name: calc-help", "description: 计算 帮助",
        "triggers: 计算", "allowed_tools: calculator"])
    registry = SkillRegistry(tmp_path)
    runtime = AgentRuntime(MockLLM(), skill_registry=registry)
    manager = runtime.skill_lifecycle
    outcome = runtime.run_task(
        "帮我 计算 27*43",
        RuntimeContext.from_settings(workspace_path=tmp_path, max_iterations=2,
                                     memory_enabled=False, knowledge_enabled=False))
    assert outcome.final_text
    # 任务结束后技能应收回 deactivated（不悬在 running/activated）
    assert manager.state_of("calc-help") == DEACTIVATED


def test_runtime_skips_skill_with_broken_dependency(tmp_path):
    from src.harness.runtime.agent_runtime import AgentRuntime
    from src.harness.runtime.run_context import RuntimeContext
    from src.llm.mock import MockLLM

    write_skill(tmp_path, "broken", ["name: broken", "description: 计算 坏依赖",
                                     "triggers: 计算", "depends: ghost"])
    registry = SkillRegistry(tmp_path)
    runtime = AgentRuntime(MockLLM(), skill_registry=registry)
    outcome = runtime.run_task(
        "帮我 计算 27*43",
        RuntimeContext.from_settings(workspace_path=tmp_path, max_iterations=2,
                                     memory_enabled=False, knowledge_enabled=False))
    assert outcome.final_text                       # 任务照常完成
    assert runtime.skill_lifecycle.state_of("broken") == DISCOVERED  # 未被激活
