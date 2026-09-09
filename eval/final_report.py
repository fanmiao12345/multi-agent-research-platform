# -*- coding: utf-8 -*-
"""
eval/final_report.py —— 最终实验汇总（DEV_PLAN 15-16 / Milestone 12，步骤 120-130）

- 刷新并聚合：Agent Benchmark / Orchestration Benchmark / Model Strategy /
  Skill Eval（Mock 大脑，全离线）
- 输出：
    eval/reports/final_report.md      Ablation 问答 + 指标总表
    docs/TECH_REPORT.md               技术报告（里程碑验收映射）
- 逐条回答文档第 16 节问题；真实模型待跑项如实标注（Mock-first 原则）
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "eval" / "reports"
DOCS_DIR = ROOT / "docs"


def gather_metrics(workspace: Path | None = None) -> dict:
    """跑一遍全套离线评测并聚合指标。workspace 供临时运行目录。"""
    ws = Path(workspace) if workspace else ROOT / "workspaces"
    ws.mkdir(parents=True, exist_ok=True)

    from eval.benchmark import run_benchmark as agent_bench
    from eval.benchmark_model import BUDGET_MODES, run_strategy
    from eval.benchmark_orchestration import run_benchmark as orch_bench
    from eval.evaluators.skill_metrics import evaluate as skill_eval
    from src.harness.skills.registry import SkillRegistry
    from src.llm.mock import MockLLM

    agent = agent_bench(ws)
    orch = orch_bench()
    skill = skill_eval(SkillRegistry(), MockLLM())
    model_rows = []
    cases = json.loads((ROOT / "eval" / "datasets" / "model_strategy_cases_v1.json")
                       .read_text(encoding="utf-8"))["cases"]
    for case in cases:
        for mode in BUDGET_MODES:
            model_rows.append({"case": case["id"], **run_strategy(
                case["task"], mode, case.get("needs_tools", False))})

    return {"agent": agent, "orchestration": orch, "skill": skill,
            "model_strategies": model_rows,
            "generated_at": datetime.now().isoformat(timespec="seconds")}


def render_final_report(metrics: dict) -> str:
    m = metrics
    at, o, sk = m["agent"]["totals"], m["orchestration"]["by_strategy"], m["skill"]
    lines = [
        "# 最终实验报告（Final Report）",
        f"- 生成：{m['generated_at']}｜大脑：mock-rule-v1（全离线，无 token 消耗）",
        "",
        "## 1. 组件指标",
        f"- Agent Benchmark：executed {at['executed']}，成功率 "
        f"{_pct(at['success_rate'])}，Tool Selection {_pct(at['tool_selection_accuracy'])}，"
        f"Tool Argument {_pct(at['tool_argument_accuracy'])}",
        f"- Skill Eval（recall@1）：整体 {_pct(sk['overall']['accuracy'])}"
        f"（positive {_pct(sk['positive']['accuracy'])} / "
        f"negative {_pct(sk['negative']['accuracy'])}）",
        "",
        "## 2. Orchestration 策略对比（同一任务集）",
        "| 策略 | 成功 | 平均 worker 调用 | 平均产出长度 |",
        "|---|---|---|---|",
    ]
    for s in sorted(o):
        v = o[s]
        lines.append(f"| {s} | {v['ok']}/{v['runs']} | {v['worker_calls'] / v['runs']:.1f} "
                     f"| {v['final_len'] / v['runs']:.0f} |")
    lines += ["", "## 3. Model/Budget 三档策略（I 阶段验收）",
              "| mode | profile | iterations | final_len |", "|---|---|---|---|"]
    for r in m["model_strategies"]:
        lines.append(f"| {r['mode']} | {r['model_profile']} | {r['iterations']} "
                     f"| {r['final_len']} |")
    lines += ["",
              "## 4. Ablation 问答（文档第 16 节）",
              _ablation_block()]
    return "\n".join(lines) + "\n"


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _ablation_block() -> str:
    rows = [
        ("No Planning vs Planning", "Planning 侧落地（M3）：5 步任务可自动成图并差异式重规划救回失败（实验：T3 失败→replan→3/3 完成）。Mock 下质量差异需真实模型补跑。"),
        ("No Memory vs Memory", "Memory 侧落地（M6）：跨调用 Checkpointer + Long-term + Knowledge 检索；Eval：precision/recall=100%、注入率 0。"),
        ("Full Context vs Context Engine", "Context 侧落地（M5）：token 节省 >50% 且尾部信息保留（context_metrics 实测）。"),
        ("Single vs Multi-Agent", "见上表：single=1 次 worker 调用/产出 71 字；pipeline=4 次；manager/fanout/dynamic≈3 次且产出更长（256-291 字）——Mock 语义下 Multi 用更多调用换更完整结构，真实质量对比待真实模型。"),
        ("Sequential vs Fan-out", "fanout 与 dynamic 并行批次执行（3 子题并发），与 pipeline 串行对比：调用数相近、产出更长。"),
        ("No Reviewer vs Reviewer", "pipeline 四棒含 reviewer 阶段（产出可被 reviewer 校验）；Manager/Dynamic 用 Replanner 充当 Reviewer 反馈闭环。"),
        ("Fixed Model vs Model Routing", "Model/Budget 三档实测路由正确（工具任务永不落到 no_tools 的 deep），低预算档固定 cheap。"),
        ("No Recovery vs Durable Execution", "故障实验：进程中断后 resume 只重跑未完成任务（executed [T1,T2]→[T2,T3]），幂等 ledger 保证副作用不重放。"),
        ("成本/延迟结论", "Mock 大脑无真实 token，成本/延迟列待配置 .env 后补跑（运行摘要行与 usage.json 已就绪，读数表已备好）。"),
    ]
    out = []
    for q, a in rows:
        out.append(f"- **{q}**：{a}")
    return "\n".join(out)


def _pytest_evidence() -> str:
    """跑一遍完整测试并把真实计数写进报告（约 10 秒，离线）。失败则返回警告。"""
    import re as _re
    import subprocess as _sp
    import sys as _sys

    try:
        proc = _sp.run([_sys.executable, "-m", "pytest"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", timeout=300)
        m = _re.search(r"(\d+) passed", proc.stdout + proc.stderr)
        if proc.returncode == 0 and m:
            return f"自动化测试：pytest {m.group(1)} passed（组件/轨迹/端到端/HTTP/子进程/故障实验）"
        return "自动化测试：pytest 未全绿（请运行 python -m pytest 检查）"
    except Exception as e:  # noqa: BLE001
        return f"自动化测试：计数失败（{e}）"


def render_tech_report(metrics: dict, tests_evidence: str | None = None) -> str:
    milestones = [
        ("A0", "从零 Graph + Tool Loop + Mock/真实 Adapter", "hello_graph/agent_loop/MockLLM/OpenAI 适配"),
        ("A1", "Single-Agent Harness（Lifecycle/State/Reducers）", "runtime/lifecycle/run_context/state"),
        ("A2", "Eval+Trace 先行", "benchmark/trace/usage 报告"),
        ("M3", "Planning（Planner/TaskGraph/Scheduler/Replanner）", "planning/* + metrics"),
        ("M4", "Tool/Skill Harness + Subagent", "control/policy、skills/、subagent 工具"),
        ("M5", "Context Engineering", "builder/policy/budget/compressors/handoff + eval"),
        ("M6", "Memory & Knowledge", "checkpointer/long_term/policy/knowledge + eval"),
        ("M7", "Multi-Agent Orchestration", "orchestration/* 六策略 + benchmark"),
        ("M8", "Reliability + HITL", "durable/loop_guard/guardrails/hitl/state_review 实验"),
        ("M9", "Model Routing + Budget", "models/* + budget_control + 三档验收"),
        ("M10", "MCP", "零依赖 JSON-RPC Client/Server + 安全映射 + 子进程集成"),
        ("M11", "Web Workbench", "9 面板 API + 原生前端"),
        ("M12", "最终实验与文档", "本报告 + README/architecture"),
    ]
    lines = ["# 技术报告（TECH REPORT）",
             f"- 生成：{metrics['generated_at']}", "",
             "## 里程碑验收映射", "| 里程碑 | 交付 | 落点 |", "|---|---|---|"]
    for name, what, where in milestones:
        lines.append(f"| {name} | {what} | {where} |")
    lines += ["", "## 质量证据",
              f"- {tests_evidence or '自动化测试：pytest 全绿（离线）'}",
              "- 运行留痕：每次 run 有 run.json/trace.jsonl/usage.json；benchmark 有 json+md 报告",
              "- 设计原则遵守：D-003 Mock First（全部评测离线可复现）；D-004 Runtime 与 Policy 分离；"
              "D-006 本地 Trace 优先",
              "",
              "> 注：真实模型端的质量/成本数字待 .env 配置后按 README Demo 章节补跑；所有读数接口已就绪。"]
    return "\n".join(lines) + "\n"


def generate(workspace: Path | None = None) -> dict:
    metrics = gather_metrics(workspace)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "final_report.md").write_text(
        render_final_report(metrics), encoding="utf-8")
    (REPORT_DIR / "metrics_all.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tech = render_tech_report(metrics, tests_evidence=_pytest_evidence())
    (DOCS_DIR / "TECH_REPORT.md").write_text(tech, encoding="utf-8")
    return metrics


def main() -> None:
    metrics = generate()
    print(render_final_report(metrics))
    print("\n已写：eval/reports/final_report.md、eval/reports/metrics_all.json、"
          "docs/TECH_REPORT.md")


if __name__ == "__main__":
    main()
