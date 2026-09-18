# -*- coding: utf-8 -*-
"""
harness/skills/lifecycle.py —— Skill 生命周期管理（Skill Harness）

四态生命周期：discovered → activated → running → deactivated
    discovered   注册表扫描到该技能（尚未启用）
    activated    依赖已解析、权限已收敛，可以注入上下文
    running      正在一次任务运行中生效（注入了 prompt）
    deactivated  显式停用（本轮任务结束后回到这里；可再次 activate）

规则：
- 迁移必须走 activate()/begin_run()/end_run()/deactivate()，非法迁移报
  SkillLifecycleError（比如 discovered 直接 running = 跳过了依赖解析）。
- activate 前自动做依赖解析：frontmatter depends 声明的技能必须存在且已
  activate；缺失/循环依赖显式报错，绝不带着悬空依赖运行。
- 热加载：registry.reload() 后调用 scan()，新文件进 discovered，
  已消失的技能移出状态表；已 activated 的技能保持状态不丢。
- 所有迁移可发布到 EventBus（type=skill_lifecycle），供 Trace/UI 订阅。
"""

from __future__ import annotations

from src.harness.skills.registry import SkillRegistry

DISCOVERED = "discovered"
ACTIVATED = "activated"
RUNNING = "running"
DEACTIVATED = "deactivated"

# 合法迁移表：状态机唯一事实来源
_TRANSITIONS = {
    DISCOVERED: {ACTIVATED},
    DEACTIVATED: {ACTIVATED},
    ACTIVATED: {RUNNING, DEACTIVATED},
    RUNNING: {ACTIVATED, DEACTIVATED},   # running→activated = end_run
}


class SkillLifecycleError(RuntimeError):
    """非法生命周期迁移或依赖解析失败。"""


class SkillLifecycleManager:
    """管理一张 skill name → 状态 的表；依赖解析在 activate 前完成。"""

    def __init__(self, registry: SkillRegistry, *, event_bus=None):
        self.registry = registry
        self.event_bus = event_bus
        self._states: dict[str, str] = {}
        self.scan()

    # ---- 扫描与查询 ----
    def scan(self) -> dict[str, str]:
        """同步注册表：新技能进 discovered，已删除技能移出；已有状态保留。"""
        known = {s.name for s in self.registry.list()}
        for name in known:
            self._states.setdefault(name, DISCOVERED)
        for name in [n for n in self._states if n not in known]:
            del self._states[name]
        return dict(self._states)

    def state_of(self, name: str) -> str:
        if name not in self._states:
            raise KeyError(f"技能 {name} 未被扫描到（先 scan/reload）")
        return self._states[name]

    def snapshot(self) -> dict:
        return dict(sorted(self._states.items()))

    # ---- 迁移 ----
    def _transition(self, name: str, target: str, *, reason: str = "") -> None:
        current = self.state_of(name)
        if target not in _TRANSITIONS[current]:
            raise SkillLifecycleError(
                f"非法迁移：{name} {current} → {target}"
                f"（允许：{', '.join(sorted(_TRANSITIONS[current])) or '无'}）")
        self._states[name] = target
        if self.event_bus is not None:
            self.event_bus.publish("skill_lifecycle", source="skill_harness",
                                   skill=name, from_state=current, to_state=target,
                                   reason=reason)

    # ---- 依赖解析 ----
    def resolve_dependencies(self, name: str) -> list[str]:
        """返回激活 name 前必须先激活的依赖（拓扑序，不含自身）；失败抛错。"""
        resolved: list[str] = []
        seen: set[str] = set()
        path: list[str] = []

        def visit(skill_name: str) -> None:
            if skill_name in seen:
                return
            if skill_name in path:
                cycle = " → ".join(path[path.index(skill_name):] + [skill_name])
                raise SkillLifecycleError(f"循环依赖：{cycle}")
            skill = self.registry.get(skill_name)
            if skill is None:
                raise SkillLifecycleError(f"依赖缺失：{skill_name}"
                                          f"（被 {path[-1] if path else name} 依赖）")
            path.append(skill_name)
            for dep in skill.depends:
                visit(dep)
            path.pop()
            seen.add(skill_name)
            if skill_name != name:
                resolved.append(skill_name)

        visit(name)
        return resolved

    # ---- 对外四个动作 ----
    def activate(self, name: str) -> None:
        """activate：先解析并激活依赖（拓扑序），再把自己迁到 activated。

        已处于 activated 时幂等（重复激活是常见调用模式，不算错误）。
        """
        for dep in self.resolve_dependencies(name):
            if self.state_of(dep) in (DISCOVERED, DEACTIVATED):
                self._transition(dep, ACTIVATED, reason=f"依赖解析（{name}）")
        if self.state_of(name) == ACTIVATED:
            return
        self._transition(name, ACTIVATED, reason="依赖解析完成")

    def begin_run(self, name: str) -> None:
        self._transition(name, RUNNING, reason="任务运行开始")

    def end_run(self, name: str) -> None:
        self._transition(name, ACTIVATED, reason="任务运行结束")

    def deactivate(self, name: str) -> None:
        self._transition(name, DEACTIVATED, reason="显式停用")


def run_skill_task(lifecycle: SkillLifecycleManager, name: str, run_fn,
                   *, deactivate_after: bool = True):
    """便捷包装：activate → begin_run → run_fn() → end_run（→ deactivate）。

    run_fn 无参调用，返回值原样透传；任何异常都会把状态收回到 activated，
    再原样抛出——保证生命周期不因业务异常悬在 running。
    """
    lifecycle.activate(name)
    lifecycle.begin_run(name)
    try:
        return run_fn()
    finally:
        lifecycle.end_run(name)
        if deactivate_after:
            lifecycle.deactivate(name)
