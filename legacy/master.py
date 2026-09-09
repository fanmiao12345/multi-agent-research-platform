# -*- coding: utf-8 -*-
"""
master.py —— 主智能体（指挥官）调度入口

组织方式：Manager–Worker + Pipeline + Fan-out/Fan-in + Handoff 的组合：
  一个「主智能体」掌握整体节奏：
    1) 评估难度 -> 决定单智能体还是多智能体（Dynamic 任务分级）
    2) 多智能体时从「角色注册表」（roles/*.md）组队并拆解步骤
    3) 研究员步骤可 Fan-out：拆成若干子主题 -> 并行 N 个研究员 -> 汇总合并
    4) 逐棒委派（带 Handoff 交接卡：来源/验收状态）并验收产物
       验收结论：PASS 下一棒 / REWORK 返工（带意见）/ ADD 补查 / FINAL 收尾
    5) 全程产物写入共享工作区（Blackboard-lite：.md + state.json）

   你的一句话任务
        │
        ▼
  [主智能体] 评估 ──┬─ single ──▶ 亲自上场（单智能体：工具+技能自动路由）
        │           └─ multi  ──▶ 组队(roles注册表) → 委派(交接卡) → 验收(节奏)
        ▼
     research 步骤可选 Fan-out：子题1、子题2、子题3 ──并行──▶ 合并 → 下一棒
        ▼
     交付最终产出 + 工作区留痕

运行方式：
    python master.py                              # 交互模式
    python master.py "任务"                        # 单次：自动评估
    python master.py "任务" --force-multi          # 强制多智能体
    python master.py "任务" --force-mock           # 无 Key 演示流程
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import agent as agent_mod   # 复用单智能体循环与大脑选择
import multi                # 复用工人循环 run_worker（角色人设走 roles 注册表）
import roles as roles_mod   # 角色注册表：动态组队的数据源
import workspace            # 共享工作区：state.json 黑板 + 产物落盘
from llm import LLM, MockLLM

# 主控自己的节奏参数
MAX_STEPS = 8      # 单任务最多委派轮次（Fan-out 的一次并行派工算一轮）
MAX_REWORK = 2     # 同一角色最多返工次数
FANOUT_PARALLEL = int(agent_mod.CFG.get("research", {}).get("parallel", "3"))

# ---------------------------------------------------------------------------
# 主智能体的三段决策提示词（真实模型用；离线模拟走代码规则兜底）
# ---------------------------------------------------------------------------
MASTER_INTAKE_SYSTEM = """你是「主智能体」：一支可伸缩智能体团队的指挥官。用户会给你一个任务，
你先判断它的难度与复杂度，决定用哪种打法，并给出理由。

两种打法：
- SINGLE：单智能体。日常问答、需要计算/查时间、写作思路梳理、审校润色一段现成文字等
  常规任务 —— 你的团队里只有你自己上场（可自动使用工具与技能）。
- MULTI：多智能体。需要联网查资料、多步研究、素材整理加长文成稿、涉及事实核验的
  严肃写作等复杂任务 —— 由你挂帅，把任务拆成若干子步骤，依次委派给子智能体执行。

输出格式（严格三行，不要其它内容）：
MODE: single 或 multi
REASON: 一句话理由
STEPS: 仅 MULTI 时需要 —— 用英文逗号列出你要委派的角色及顺序。
可用角色（按需挑选，可重复不用）：researcher、organizer、writer、editor"""

MASTER_SPLIT_SYSTEM = """你是「主智能体」。接下来要派研究员子智能体做检索。请判断：这个研究主题
是否值得拆成若干相互独立的子主题，并行派出多个研究员（Fan-out）？

- 值得拆（主题大、含多个可独立查询的方面）：输出一行
  TOPICS: 子题一、子题二、子题三（2~3 个，顿号分隔，每个尽量短）
- 主题足够聚焦、拆开反而低效：输出一行
  TOPICS: 无需拆分"""

MASTER_REVIEW_SYSTEM = """你是「主智能体」。刚刚一个子智能体交回了它的产物，请验收这份产物，
对照用户的总任务判断：
1) 是否回答了任务的核心问题？有没有硬伤（编造来源、张冠李戴、关键环节缺失）？
2) 是否需要让同一个子智能体返工（给具体意见）？
3) 是否需要临时加派 researcher 补查缺口（给出补查任务）？
4) 是否可以过关，由你收尾交付？

输出格式（严格三行，不要其它内容）：
VERDICT: pass 或 rework 或 add 或 final
FEEDBACK: 给子智能体的具体意见（没有就写 无）
NEXT: 仅 VERDICT=add 时需要 —— 给 researcher 的补查任务一句话"""

_BUDGET_TAGS = {"research": None, "default": None}  # None 表示用 agent 默认，见下


def _role_info() -> dict:
    """从角色注册表现读所有角色（热插拔），返回 {name: {system,use_tools,budget,...}}。"""
    info = {}
    for name in roles_mod.list_roles():
        role = roles_mod.load_role(name)
        if not role or not role["system"]:
            continue
        tag = role.get("budget", "default")
        budget = (agent_mod.RESEARCH_MAX_ROUNDS if tag == "research"
                  else agent_mod.MAX_ROUNDS)
        info[name] = {"system": role["system"], "use_tools": role["use_tools"],
                      "budget": budget, "title": role.get("title", name)}
    if not info:  # 注册表为空时的兜底（理论上不会发生）
        info = {"researcher": {"system": multi.RESEARCHER_SYSTEM, "use_tools": True,
                               "budget": multi.RESEARCH_MAX_ROUNDS, "title": "研究员 Agent"}}
    return info


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _chat_text(llm, system: str, user: str, label: str,
               stats: dict | None = None) -> str:
    try:
        reply = llm.chat([{"role": "system", "content": system},
                          {"role": "user", "content": user}], tools=[])
        if stats is not None:
            agent_mod._absorb_reply(stats, reply)
        return reply.get("content") or ""
    except Exception as e:
        print(f"  [主智能体] {label}调用失败（{e}），走兜底")
        return ""


def _parse_labels(text: str) -> dict[str, str]:
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\s*([A-Za-z_]+)\s*[:：]\s*(.*?)\s*$", line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


# ---------------------------------------------------------------------------
# ① 难度评估：SINGLE or MULTI + 组队步骤
# ---------------------------------------------------------------------------
def evaluate_mode(llm, task: str, role_names: list[str],
                  stats: dict | None = None) -> dict:
    if isinstance(llm, MockLLM):
        triggers = ("调研", "查资料", "资料", "搜索", "现状", "研究", "写一篇", "写文章",
                    "成稿", "公众号", "研究报告", "综述", "多智能体")
        if any(k in task for k in triggers):
            steps = ["researcher", "organizer", "writer", "editor"]
            steps = [s for s in steps if s in role_names] or ["researcher"]
            return {"mode": "multi", "reason": "关键词规则：任务涉及查资料/成稿等复杂环节",
                    "steps": steps}
        return {"mode": "single", "reason": "关键词规则：未检测到需要多角色协作的信号", "steps": []}

    text = _chat_text(llm, MASTER_INTAKE_SYSTEM, f"用户任务：{task}", "评估",
                      stats=stats)
    labels = _parse_labels(text)
    mode = labels.get("mode", "").strip().lower()
    if mode not in ("single", "multi"):
        print(f"  [主智能体] 评估输出未按格式返回（{text[:80]}…），按 single 处理")
        return {"mode": "single", "reason": "评估格式异常，退回单智能体", "steps": []}
    steps = [s.strip() for s in re.split(r"[，,、]", labels.get("steps", "")) if s.strip()]
    steps = [s for s in steps if s in role_names]
    if mode == "multi" and not steps:
        steps = [s for s in ("researcher", "organizer", "writer", "editor")
                 if s in role_names] or ["researcher"]
    return {"mode": mode, "reason": labels.get("reason", ""), "steps": steps}


# ---------------------------------------------------------------------------
# ② Fan-out 规划：值得拆就返回子题列表，否则 None
# ---------------------------------------------------------------------------
def plan_subtopics(llm, task: str, stats: dict | None = None) -> list[str] | None:
    if isinstance(llm, MockLLM):
        if "并行" not in task and "子题" not in task:
            return None
        mid = max(len(task) // 2, 1)
        return [task[:mid].strip() or task, task[mid:].strip() or task][:2]

    text = _chat_text(llm, MASTER_SPLIT_SYSTEM, f"研究主题：{task}", "拆题",
                      stats=stats)
    labels = _parse_labels(text)
    raw = labels.get("topics", "").strip()
    if not raw or "无需拆分" in raw:
        return None
    topics = [t.strip() for t in re.split(r"[，,、；;]", raw) if t.strip()]
    topics = topics[:FANOUT_PARALLEL]
    return topics if len(topics) >= 2 else None


# ---------------------------------------------------------------------------
# ③ Handoff 交接卡
# ---------------------------------------------------------------------------
def _handoff_card(records: dict, deps: list[str]) -> str:
    """按依赖角色组装交接卡（来源文件 + 验收状态），没有历史就不加。"""
    lines = []
    for d in deps:
        r = records.get(d)
        if r:
            lines.append(f"- 上一步「{d}」→ {r.get('file', '?')}"
                         f"（验收：{r.get('verdict') or '—'}）")
    return ("【交接卡】本步的输入来源：\n" + "\n".join(lines) + "\n\n") if lines else ""


def build_delegate_task(role: str, task: str, spec: dict, artifacts: dict,
                        records: dict | None = None, feedback: str = "") -> str:
    """按角色组装子智能体任务（含 Handoff 交接卡与返工意见）。"""
    records = records or {}
    if role == "researcher":
        return (_handoff_card(records, []) +
                f"研究主题：{task}\n补充背景：{spec.get('extra') or '无'}\n"
                f"请开始检索，最后输出《原始资料》。")
    if role == "organizer":
        return (_handoff_card(records, ["researcher"]) +
                f"下面是研究员交来的《原始资料》：\n\n{artifacts.get('researcher', '（无）')}"
                f"\n\n请输出《素材包》。")
    if role == "writer":
        material = (artifacts.get("organizer") or artifacts.get("researcher")
                    or "（主智能体未派资料类子智能体：请基于你的知识成稿，但不要编造来源）")
        base = (_handoff_card(records, ["organizer", "researcher"]) +
                f"研究主题：{task}\n成稿要求：体裁={spec.get('kind', '公众号推文')}；"
                f"读者={spec.get('audience', '普通大众')}；约 {spec.get('words', '1200')} 字。"
                f"{spec.get('extra') or ''}\n\n《素材包》如下：\n\n{material}\n\n请直接输出成稿。")
        return base + (f"\n\n主智能体要求返工，意见如下，请据此修订后重新输出全文：\n{feedback}"
                       if feedback else "")
    if role == "editor":
        return (_handoff_card(records, ["writer", "organizer", "researcher"]) +
                f"下面是成稿 v1：\n\n{artifacts.get('writer', '（无）')}"
                f"\n\n下面是对照用的《素材包》：\n\n{artifacts.get('organizer') or artifacts.get('researcher', '（无）')}")
    return task


# ---------------------------------------------------------------------------
# ④ 验收
# ---------------------------------------------------------------------------
def review_result(llm, task: str, role: str, artifact: str, is_last: bool,
                  spec: dict, stats: dict | None = None) -> dict:
    if isinstance(llm, MockLLM):
        return {"verdict": "final" if is_last else "pass", "feedback": "无", "next": ""}

    text = _chat_text(
        llm, MASTER_REVIEW_SYSTEM,
        f"用户总任务：{task}\n体裁等要求：{spec.get('kind', '公众号推文')} / "
        f"{spec.get('audience', '普通大众')} / 约{spec.get('words', '1200')}字\n"
        f"刚验收的角色：{role}\n该角色的产物：\n\n{artifact[:6000]}\n\n"
        f"（若这是你计划中的最后一步且产物过关，请用 VERDICT: final）",
        "验收", stats=stats)
    labels = _parse_labels(text)
    verdict = labels.get("verdict", "").lower()
    if verdict not in ("pass", "rework", "add", "final"):
        verdict = "final" if is_last else "pass"
    return {"verdict": verdict, "feedback": labels.get("feedback", "无"),
            "next": labels.get("next", "")}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_master(llm, task: str, spec: dict | None = None,
               force_mode: str | None = None, quiet: bool = False,
               stats: dict | None = None) -> str:
    """主智能体跑完一个任务，返回最终产出文本。spec：kind/audience/words/extra。

    quiet：隐藏委派/验收等节奏详情；stats：就地累计主控 + 各子智能体用量（A3）。
    """
    spec = dict(spec or {})
    for k, v in (("kind", "公众号推文"), ("audience", "普通大众"),
                 ("words", "1200"), ("extra", "")):
        spec.setdefault(k, v)
    if stats is None:
        stats = agent_mod._new_stats()
    info_print = (lambda *a: None) if quiet else print

    roles_info = _role_info()
    role_names = list(roles_info)
    info_print(f"\n[主智能体] 收到任务：{task}")
    eval_info = (evaluate_mode(llm, task, role_names, stats) if force_mode is None
                 else {"mode": force_mode, "reason": "用户指定", "steps": []})
    if force_mode == "multi" and not eval_info["steps"]:
        eval_info["steps"] = [s for s in ("researcher", "organizer", "writer", "editor")
                              if s in role_names] or list(role_names)
    info_print(f"[主智能体] 难度评估 → {eval_info['mode'].upper()}（{eval_info['reason']}）")

    # ---- 路线一：单智能体（主控亲自上场）----
    if eval_info["mode"] == "single":
        answer, _ = agent_mod.run_agent(llm, task,
                                        system_prompt=agent_mod.SYSTEM_PROMPT,
                                        auto_skills=True, stats=stats)
        agent_mod.print_summary("主控·单智能体", stats)
        return answer

    # ---- 路线二：多智能体（挂帅调度，产物进共享工作区）----
    ws = workspace.new_run_dir(task, engine="master")
    workspace.init_state(ws, task, spec, engine="master")
    plan = list(eval_info["steps"])
    artifacts: dict[str, str] = {}
    records: dict[str, dict] = {}          # role -> {file, verdict, feedback}
    reworks: dict[str, int] = defaultdict(int)
    feedback = ""
    total = 0
    idx = 0
    final_text = ""
    split_done = False                      # Fan-out 只做一次（防返工时重复拆）
    while idx < len(plan) and total < MAX_STEPS * 2:
        role = plan[idx]
        info = roles_info.get(role)
        if info is None:
            idx += 1
            continue
        total += 1
        seq = len(records) + 1
        info_print(f"\n[主智能体] 委派 → 子智能体「{info['title']}」"
                   f"（第 {seq} 棒；步骤 {idx + 1}/{len(plan)}）")

        # ---- Fan-out：研究员步骤尝试并行拆解 ----
        if role == "researcher" and not split_done:
            topics = plan_subtopics(llm, task, stats)
            if topics:
                split_done = True
                info_print(f"[主智能体] Fan-out：拆成 {len(topics)} 个子主题并行研究"
                           f"（最多同时 {FANOUT_PARALLEL} 个）")
                outs: list[str] = []
                with ThreadPoolExecutor(max_workers=min(len(topics), FANOUT_PARALLEL)) as ex:
                    futs = []
                    for i, sub in enumerate(topics, 1):
                        sub_task = (f"研究子主题（{i}/{len(topics)}）：{sub}\n"
                                    f"（总任务：{task}）请只围绕该子主题检索，"
                                    f"最后输出该子主题的《原始资料》部分。")
                        futs.append(ex.submit(
                            multi.run_worker, llm, info["system"], sub_task,
                            info["use_tools"], info["budget"], quiet, stats))
                    for i, (sub, fut) in enumerate(zip(topics, futs), 1):
                        text, _ = fut.result()
                        workspace.record_artifact(ws, f"research_sub{i}.md", text,
                                                  role="researcher", verdict="collected")
                        outs.append(f"### 子主题 {i}：{sub}\n\n{text}")
                combined = "\n\n".join(outs)
                artifacts["researcher"] = combined
                workspace.record_artifact(ws, "01_researcher_combined.md", combined,
                                          role="researcher", verdict="collected")
                # 并行批次后由主控统一验收合并产物
                verdict = review_result(llm, task, role, combined,
                                        is_last=(idx == len(plan) - 1), spec=spec,
                                        stats=stats)
                records[role] = {"file": "01_researcher_combined.md",
                                 "verdict": verdict["verdict"]}
                info_print(f"[主智能体] 验收「researcher(合并)」→ {verdict['verdict'].upper()}"
                           f"（{verdict['feedback'][:100]}）")
                if verdict["verdict"] == "final":
                    final_text = artifacts.get("writer") or combined
                    break
                if verdict["verdict"] == "rework":
                    if reworks[role] < MAX_REWORK:
                        feedback = verdict["feedback"]
                        continue        # 返工单走下面的普通研究员路径
                idx += 1
                continue

        delegate_task = build_delegate_task(role, task, spec, artifacts, records, feedback)
        out, _ = multi.run_worker(llm, info["system"], delegate_task,
                                  use_tools=info["use_tools"],
                                  max_rounds=info["budget"], quiet=quiet, stats=stats)
        artifacts[role] = out
        reworks[role] += 1

        verdict = review_result(llm, task, role, out,
                                is_last=(idx == len(plan) - 1), spec=spec, stats=stats)
        rec_file = f"{seq:02d}_{role}.md"
        workspace.record_artifact(ws, rec_file, out, role=role,
                                  verdict=verdict["verdict"], feedback=verdict["feedback"])
        records[role] = {"file": rec_file, "verdict": verdict["verdict"]}
        info_print(f"[主智能体] 验收「{role}」→ {verdict['verdict'].upper()}"
                   f"（{verdict['feedback'][:100]}）")
        if verdict["verdict"] == "final":
            final_text = artifacts.get("writer") or artifacts.get("organizer") \
                or artifacts.get("researcher") or out
            break
        if verdict["verdict"] == "rework" and reworks[role] < MAX_REWORK:
            feedback = verdict["feedback"]
            continue
        if verdict["verdict"] == "add":
            extra = [r for r in ("researcher", "organizer") if r not in plan]
            if extra:
                plan[idx + 1:idx + 1] = extra
                info_print(f"[主智能体] 补查：临时加派 {extra} 后继续")
                feedback = verdict["next"]
                continue
        feedback = ""
        idx += 1
    else:
        final_text = artifacts.get("writer") or artifacts.get("organizer") \
            or artifacts.get("researcher") or "（子智能体未能产出可用内容）"

    workspace.set_final(ws, "final.md", final_text)
    agent_mod.print_summary("主控·多智能体", stats,
                            extra=f"｜委派 {total} 次｜工作区 {ws}")
    info_print(f"\n[主智能体] 多智能体路线完成，共委派 {total} 次")
    return final_text


def main():
    parser = argparse.ArgumentParser(
        description="主智能体：评估难度 → 单/多智能体路由 → 挂帅调度（可 Fan-out 并行）")
    parser.add_argument("task", nargs="?", help="任务描述（不带则进入交互模式）")
    parser.add_argument("--kind", default="公众号推文", help="（多智能体成稿用）体裁")
    parser.add_argument("--audience", default="普通大众", help="目标读者")
    parser.add_argument("--words", default="1200", help="目标字数")
    parser.add_argument("--extra", default="", help="其它要求")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--force-single", action="store_true", help="强制走单智能体")
    group.add_argument("--force-multi", action="store_true", help="强制走多智能体")
    parser.add_argument("--api-key", help="DeepSeek API Key（也可用环境变量 DEEPSEEK_API_KEY）")
    parser.add_argument("--force-mock", action="store_true", help="强制使用离线模拟大脑")
    parser.add_argument("--quiet", action="store_true",
                        help="隐藏委派/验收等节奏详情，只保留问答与摘要")
    args = parser.parse_args()

    spec = {"kind": args.kind, "audience": args.audience, "words": args.words,
            "extra": args.extra}
    force_mode = "multi" if args.force_multi else ("single" if args.force_single else None)

    print("=" * 60)
    print(" 主智能体（指挥官）：难度评估 → 单/多路由 → 组队挂帅 → 并行研究 → 逐棒验收")
    print("=" * 60)
    llm, key_source = agent_mod._pick_llm(
        argparse.Namespace(api_key=args.api_key, force_mock=args.force_mock))
    if isinstance(llm, LLM):
        print(f" 大脑: {llm.model}（Key 来源: {key_source}）")
    else:
        print(" 大脑: MockLLM（离线模拟：流程可跑通，决策与产出为规则占位）")

    if args.task:
        print()
        print(f"[主智能体] {run_master(llm, args.task, spec, force_mode,
                                       quiet=args.quiet)}")
        return

    print(" 进入主智能体交互（exit 退出）。例如：帮我调研一下深海采矿并写一篇公众号文章")
    while True:
        try:
            task = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break
        if not task:
            continue
        if task.lower() in {"exit", "quit", "退出"}:
            print("再见！")
            break
        print(f"\n[主智能体] {run_master(llm, task, spec, force_mode, quiet=args.quiet)}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(130)
    except RuntimeError as e:
        print(f"\n[错误] {e}", file=sys.stderr)
        sys.exit(1)
