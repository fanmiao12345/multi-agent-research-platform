# -*- coding: utf-8 -*-
"""
multi.py —— 多智能体「查资料 → 整理 → 写作」流水线

把一件事拆给多个各司其职的 Agent，每个 Agent 有自己的上下文与（可选）小循环，
后一位把前一位的产出当作输入 —— 这就是多智能体的「消息传递」协作。

角色分工：
  ① 研究员(带工具)  多次联网检索 + 精读原文 -> 《原始资料》
  ② 整理师(纯脑力)  提炼成《素材包》：论点/证据/数据/缺口
  ③ 撰稿人(纯脑力)  按体裁/读者/字数写成稿 v1
  ④ 审校(纯脑力)    对照素材查错；有意见则撰稿人修订 v2

运行方式：
    python multi.py                          # 向导式：一步步问你主题/体裁/读者/字数
    python multi.py "主题一句话"              # 直接开跑（其余用默认值）
    python multi.py "主题" --kind 公众号推文 --audience 20-35岁职场人 --words 1200
    python multi.py "主题" --force-mock       # 无 Key 也能看流程（内容为占位）

产出：research_output/<时间戳_主题>/ 下的 00~05 号文件，最终成稿在 final.md。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import tools
import agent as agent_mod  # 复用大脑选择、技能目录等；不产生循环导入
import roles as roles_mod  # 角色注册表：roles/*.md（热插拔）
import workspace           # 共享工作区：黑板 state.json + 产物落盘
from llm import LLM, MockLLM

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESEARCH_OUT = os.path.join(BASE_DIR, "research_output")
# 研究员轮数预算与单智能体的 deep-research 技能一致（config.ini [research] 可调）
RESEARCH_MAX_ROUNDS = int(getattr(agent_mod, "RESEARCH_MAX_ROUNDS", 20))

# ---------------------------------------------------------------------------
# 角色人设托管在 roles/ 注册表（roles/*.md，像 skills/ 一样可热插拔）：
# 改 roles/researcher.md 等文件即改人设，不用动代码。
# 注意：离线模拟大脑会按关键词触发工具，所以这些手册刻意避开了
# 天气/备忘/计算等触发词 —— 换真实模型后它们只是普通文本，无需担心。
# ---------------------------------------------------------------------------
RESEARCHER_SYSTEM = roles_mod.role_system("researcher")
ORGANIZER_SYSTEM = roles_mod.role_system("organizer")
WRITER_SYSTEM = roles_mod.role_system("writer")
EDITOR_SYSTEM = roles_mod.role_system("editor")

_WORKER_MAX_ROUNDS = 8  # 纯脑力工人单轮上限（无工具角色一般 1 轮即收；研究员另用大预算）


def run_worker(llm, system_prompt: str, task: str, use_tools: bool,
               max_rounds: int = _WORKER_MAX_ROUNDS, quiet: bool = False,
               stats: dict | None = None) -> tuple[str, list]:
    """跑一个「工人 Agent」：带自己的 system 人设跑一遍小循环。

    本质和 agent.py 的 run_agent 同构：问大脑 -> 有工具请求就执行并回喂 ->
    直到它直接给出文本。多智能体 = 把这段循环分别跑在不同人设上。
    quiet：隐藏调用细节；stats：可选，就地累计 LLM/工具/token 用量（复用 agent 统计）。
    返回 (最终文本, 该工人的完整消息流水账)。
    """
    if stats is None:
        stats = {}
    for _k in ("llm_calls", "tool_calls", "rounds", "prompt_tokens", "completion_tokens"):
        stats.setdefault(_k, 0)
    stats.setdefault("tokens_known", False)
    stats.setdefault("started", time.perf_counter())

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]
    schemas = tools.TOOL_SCHEMAS if use_tools else None
    final = ""
    used_rounds = 0
    for step in range(1, max_rounds + 1):
        used_rounds = step
        reply = llm.chat(messages, tools=schemas)
        agent_mod._absorb_reply(stats, reply)
        calls = reply.get("tool_calls")
        if not calls:
            final = reply.get("content") or "（该工人没有给出回答）"
            break
        assistant_msg = {
            "role": "assistant",
            "content": reply.get("content"),
            "tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"],
                              "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                for c in calls
            ],
        }
        messages.append(assistant_msg)
        for c in calls:
            if not quiet:
                print(f"      · 调用 {c['name']}({c['arguments']})")
            messages.append({"role": "tool", "tool_call_id": c["id"],
                             "content": tools.run_tool(c["name"], c["arguments"])})
            stats["tool_calls"] += 1
    else:
        # 预算用尽也不空手：禁止再调工具，把已收集内容做一次收尾总结
        if not quiet:
            print(f"      [预算用尽] 已达 {max_rounds} 轮上限，对该工人做收尾总结…")
        wrap = list(messages) + [{
            "role": "user",
            "content": "轮数预算已用尽。请只基于上面已经拿到的内容做收尾总结："
                       "能确认的信息整理成你的交付物（《原始资料》/《素材包》/成稿），"
                       "缺口明确列出；不要再调用工具，不要编造。",
        }]
        try:
            reply = llm.chat(wrap, tools=[])
            agent_mod._absorb_reply(stats, reply)
            final = "（预算用尽，收尾交付，可能不完整）\n\n" + (reply.get("content")
                                                               or "（收尾总结未给出内容）")
        except Exception as e:
            final = f"（该工人收尾失败：{e}）"
    stats["rounds"] = stats.get("rounds", 0) + used_rounds
    stats["elapsed"] = time.perf_counter() - stats["started"]
    return final, messages


def run_pipeline(llm, spec: dict, quiet: bool = False) -> str:
    """执行整条固定流水线（Pipeline 模式），返回 final.md 路径。

    产物全部写进共享工作区（workspace：.md 文件 + state.json 黑板）。
    """
    topic = spec["topic"]
    ws = workspace.new_run_dir(topic, engine="pipeline")
    workspace.init_state(ws, topic, spec, engine="pipeline")
    stats = agent_mod._new_stats()  # 流水线累计统计（A3 摘要）

    def phase(no: int, title: str) -> None:
        if not quiet:
            print(f"\n{'─' * 56}\n  阶段 {no}｜{title}\n{'─' * 56}")

    # 0) 任务书
    phase(0, "任务书")
    task_md = (f"# 研究任务书\n\n- 主题：{topic}\n- 体裁：{spec['kind']}\n"
               f"- 读者：{spec['audience']}\n- 目标字数：约 {spec['words']} 字\n"
               f"- 补充要求：{spec.get('extra') or '（无）'}")
    workspace.record_artifact(ws, "00_task.md", task_md, role="task", verdict="pass")

    # 1) 研究员（唯一带工具的角色；查资料天生多轮，用 research 大预算，
    #    预算用尽时 run_worker 会给部分资料而不是空手）
    phase(1, "研究员 Agent：联网查资料")
    notes, _ = run_worker(llm, RESEARCHER_SYSTEM,
                          f"研究主题：{topic}\n补充背景：{spec.get('extra') or '无'}\n"
                          f"请开始检索，最后输出《原始资料》。",
                          use_tools=True, max_rounds=RESEARCH_MAX_ROUNDS,
                          quiet=quiet, stats=stats)
    workspace.record_artifact(ws, "01_raw_notes.md", notes, role="researcher",
                              verdict="pass")

    # 2) 整理师
    phase(2, "整理师 Agent：提炼素材包")
    material, _ = run_worker(llm, ORGANIZER_SYSTEM,
                             f"下面是研究员交来的《原始资料》：\n\n{notes}\n\n请输出《素材包》。",
                             use_tools=False, quiet=quiet, stats=stats)
    workspace.record_artifact(ws, "02_material.md", material, role="organizer",
                              verdict="pass")

    # 3) 撰稿人 v1
    phase(3, "撰稿人 Agent：成稿 v1")
    writer_task = (f"研究主题：{topic}\n成稿要求：体裁={spec['kind']}；读者={spec['audience']}；"
                   f"约 {spec['words']} 字。{spec.get('extra') or ''}\n\n"
                   f"下面是《素材包》：\n\n{material}\n\n请直接输出成稿。")
    draft, _ = run_worker(llm, WRITER_SYSTEM, writer_task, use_tools=False,
                          quiet=quiet, stats=stats)
    workspace.record_artifact(ws, "03_draft_v1.md", draft, role="writer", verdict="pass")

    # 4) 审校
    phase(4, "审校 Agent：对照素材查错")
    review, _ = run_worker(llm, EDITOR_SYSTEM,
                           f"下面是成稿 v1：\n\n{draft}\n\n下面是对照用的《素材包》：\n\n{material}",
                           use_tools=False, quiet=quiet, stats=stats)
    workspace.record_artifact(ws, "04_review.md", review, role="editor")

    # 5) 修订 v2（审校说「需修改」时）
    final = draft
    if "通过" not in review and "（模拟" not in review:
        phase(5, "撰稿人 Agent：按审校意见修订 v2")
        revised, _ = run_worker(llm, WRITER_SYSTEM,
                                writer_task + f"\n\n审校意见如下，请据此修订后重新输出全文：\n{review}",
                                use_tools=False, quiet=quiet, stats=stats)
        final = revised
        workspace.record_artifact(ws, "05_final.md",
                                  f"审校意见：\n{review}\n\n-----\n\n{final}",
                                  role="writer", verdict="final")
    else:
        phase(5, "审校通过，无需修订")
        workspace.record_artifact(ws, "05_final.md",
                                  f"审校：通过\n\n{f'（审校意见：{review}）' if review.strip() != '通过' else ''}",
                                  role="editor", verdict="final")
    final_path = workspace.set_final(ws, "final.md", final)
    agent_mod.print_summary("固定流水线", stats, extra=f"｜工作区 {ws}")
    return final_path


def _wizard() -> dict:
    print("小智·多智能体写作工坊 —— 先回答几个问题（直接回车用默认值）：")
    spec = {
        "topic": input("  ① 研究主题（一句话）：").strip(),
        "kind": input("  ② 成品体裁 [公众号推文]：").strip() or "公众号推文",
        "audience": input("  ③ 目标读者 [普通大众]：").strip() or "普通大众",
        "words": input("  ④ 目标字数 [1200]：").strip() or "1200",
        "extra": input("  ⑤ 其他要求（语气/结构/是否要建议等，可空）：").strip(),
    }
    if not spec["topic"]:
        print("主题不能为空。")
        sys.exit(1)
    return spec


def main():
    parser = argparse.ArgumentParser(
        description="多智能体流水线：查资料 → 整理 → 写作（研究主题一句话即可开跑）")
    parser.add_argument("topic", nargs="?", help="研究主题（不带则进入向导模式）")
    parser.add_argument("--kind", default="公众号推文", help="成品体裁，如：公众号推文/研究报告/知乎回答")
    parser.add_argument("--audience", default="普通大众", help="目标读者")
    parser.add_argument("--words", default="1200", help="目标字数")
    parser.add_argument("--extra", default="", help="其他要求")
    parser.add_argument("--api-key", help="DeepSeek API Key（也可用环境变量 DEEPSEEK_API_KEY）")
    parser.add_argument("--force-mock", action="store_true", help="强制使用离线模拟大脑")
    parser.add_argument("--quiet", action="store_true",
                        help="隐藏阶段与工具细节，只保留关键输出")
    args = parser.parse_args()

    spec = {"topic": args.topic, "kind": args.kind, "audience": args.audience,
            "words": args.words, "extra": args.extra}
    if not spec["topic"]:
        spec = _wizard()

    print("=" * 60)
    print(" 多智能体流水线：研究员 → 整理师 → 撰稿人 → 审校")
    print(" 每个角色独立上下文，产出逐棒传递（消息传递式协作）")
    print("=" * 60)

    # 复用 agent.py 的大脑选择逻辑（命令行 Key > 环境变量 > config.ini > 常量）
    llm, key_source = agent_mod._pick_llm(
        argparse.Namespace(api_key=args.api_key, force_mock=args.force_mock))
    if isinstance(llm, LLM):
        print(f" 大脑: {llm.model}（Key 来源: {key_source}）")
    else:
        print(" 大脑: MockLLM（离线模拟：流程可跑通，资料与成稿为占位，"
              "想要真实内容请配置 Key）")
    print(f" 任务: {spec['topic']}")
    print(f" 体裁: {spec['kind']} | 读者: {spec['audience']} | 字数: 约 {spec['words']} 字")

    final_path = run_pipeline(llm, spec, quiet=args.quiet)

    print("\n" + "=" * 60)
    print(" 流水线完成！产出目录：")
    import glob
    for p in sorted(glob.glob(os.path.join(os.path.dirname(final_path), "*.md"))):
        print(f"   {os.path.basename(p)}")
    print(f"\n 终稿全文如下（已存 final.md）：\n{'─' * 60}")
    with open(os.path.join(os.path.dirname(final_path), "final.md"), "r", encoding="utf-8") as f:
        print(f.read())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(130)
    except RuntimeError as e:
        print(f"\n[错误] {e}", file=sys.stderr)
        sys.exit(1)
