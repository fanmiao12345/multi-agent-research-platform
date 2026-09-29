# -*- coding: utf-8 -*-
"""
build_v3_from_public.py —— 批次 A：公开数据集 → v3 业务案例转换（纯标准库）

来源（docs/PUBLIC_DATASETS_CANDIDATES.md §四 批次 A）：
- CMRC 2018 dev（中文抽取式 MRC，答案即逐字片段）→ 20 例 answerable
  （expected_outcome=final，mechanisms=["evidence_location"]）
  仓库 https://github.com/ymcui/cmrc2018（CC BY-SA 4.0），文件 data/cmrc2018_dev.json
- DuReader_robust dev（中文 MRC 鲁棒性集，含 unanswerable）→ 10 例无据拒答
  （expected_outcome=unable，mechanisms=["refuse_without_evidence"]）
  官方分发 https://dataset-bj.cdn.bcebos.com/dureader_robust/data/dureader_robust-data.tar.gz
  （随包 License.docx：仅供研究用途；对外分发前须复核）

设计约束：
- 不改动 research_writing_v1.json（v1 冻结分母 + v2 扩充不动；测试锁定 33+10/version=2）；
  v3 独立成文件 eval/datasets/research_writing_v3_public.json，schema 与 v1 相同。
- 每例 facts[].quote 必须在对应 sources[].text 中逐字命中（本脚本断言 + research_cases 复核）。
- 确定性采样（固定 seed），映射记录写入 meta.conversion。
- 运行：.venv/Scripts/python scripts/build_v3_from_public.py [--raw .tmp/v3_public] [--out <路径>]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = 20260929
MAX_CONTEXT_CHARS = 1200          # 单来源正文上限（可读性；远低于 2MB 存储上限）
CMRC_TARGET = 20
DUREADER_TARGET = 10

_SYS = __import__("sys")
if str(ROOT / "scripts") not in _SYS.path:
    _SYS.path.insert(0, str(ROOT))
sys.path = _SYS.path


def _stable(shuffle_key: str) -> int:
    """不依赖 random 模块的确定性排序键（可复现、跨平台一致）。"""
    return int(uuid.uuid5(uuid.NAMESPACE_URL, shuffle_key).int % (2 ** 31))


def load_cmrc(raw: Path) -> list[dict]:
    data = json.loads(io.open(raw / "cmrc2018_dev.json", encoding="utf-8").read())
    cases = []
    for item in data:
        context = item["context_text"]
        if not (200 <= len(context) <= MAX_CONTEXT_CHARS):
            continue
        for qa in item["qas"]:
            # 原始文件混有 NaN（解析为 float）等脏答案，只接受非空字符串且标注一致
            answers = [a for a in qa.get("answers", [])
                       if isinstance(a, str) and a.strip()]
            # 只取多名标注完全一致的答案：逐字 span 口径干净，避免采样争议
            if not answers or len(set(answers)) != 1:
                continue
            answer = answers[0]
            if not (4 <= len(answer) <= 60):
                continue
            if answer not in context:
                continue
            cases.append({"context_id": item["context_id"], "query_id": qa["query_id"],
                          "query_text": qa["query_text"].strip(),
                          "context": context, "answer": answer})
    cases.sort(key=lambda c: _stable("cmrc2018/" + c["query_id"]))
    return cases


def load_dureader(raw: Path) -> list[dict]:
    # 官方 train/dev 仅含 answerable；unanswerable 构造题在 test1.json（answers 全空）
    data = json.loads(io.open(raw / "dureader_robust-test1" / "test1.json",
                              encoding="utf-8").read())["data"]
    cases = []
    for article in data:
        for para in article["paragraphs"]:
            context = para["context"]
            if not (150 <= len(context) <= MAX_CONTEXT_CHARS):
                continue
            for qa in para["qas"]:
                answers = qa.get("answers") or []
                # unanswerable 口径：无答案或答案为空串（DuReader_robust 官方标注）
                if any((a.get("text") or "").strip() for a in answers):
                    continue
                question = qa["question"].strip()
                if not question or not any("\u4e00" <= ch <= "\u9fff" for ch in question):
                    continue
                cases.append({"raw_id": qa["id"], "question": question,
                              "context": context, "title": article.get("title", "")})
    cases.sort(key=lambda c: _stable("dureader_robust/" + c["raw_id"]))
    return cases


def build_sources_and_tasks(cmrc: list[dict], dureader: list[dict]) -> tuple[list, list]:
    sources, tasks = [], []
    for i, c in enumerate(cmrc[:CMRC_TARGET], start=1):
        sid = f"s4{i:02d}"
        tid = f"pc{i:02d}"
        sources.append({"id": sid, "title": f"CMRC 材料 {c['context_id']}",
                        "date": "unknown", "text": c["context"],
                        "origin": "public_dataset:cmrc2018",
                        "url": "https://github.com/ymcui/cmrc2018"})
        assert c["answer"] in c["context"], f"quote 未逐字命中：{tid}"
        tasks.append({
            "id": tid, "category": "research", "title": c["query_text"][:30],
            "status": "not_run", "input_kind": "local", "source_ids": [sid],
            "request": f"根据材料回答问题，并给出可定位的依据：{c['query_text']}",
            "sections": ["结论", "依据与定位"],
            "facts": [{"source_id": sid, "quote": c["answer"], "claim": c["answer"]}],
            "conflicts": [], "gaps": [],
            "forbidden_claims": ["编造材料中不存在的数字或细节", "虚构出处、页码或作者"],
            "expected_outcome": "final", "batch": "v3",
            "mechanisms": ["evidence_location"]})
    for i, c in enumerate(dureader[:DUREADER_TARGET], start=1):
        sid = f"s5{i:02d}"
        tid = f"pu{i:02d}"
        # 锚点事实：取材料首句（到第一个句号为止，前缀必然是原文子串）——
        # 与 v1 的 r07/r12 模式一致：unable 案例也带"已有信息"锚点，
        # 逼报告先如实引用相关材料、再声明核心问题无据（锚点≠答案）
        cut = c["context"].find("。")
        anchor = c["context"][:cut + 1] if cut >= 0 else c["context"][:50]
        anchor = anchor.strip()
        assert anchor and anchor in c["context"]
        sources.append({"id": sid, "title": f"DuReader 材料 {c['title'][:20]}",
                        "date": "unknown", "text": c["context"],
                        "origin": "public_dataset:dureader_robust",
                        "url": "https://github.com/PaddlePaddle/DuReader-robust/issues"})
        tasks.append({
            "id": tid, "category": "research", "title": c["question"][:30],
            "status": "not_run", "input_kind": "local", "source_ids": [sid],
            "request": f"根据材料回答问题，并给出可定位的依据：{c['question']}",
            "sections": ["已有信息", "无法完成的部分"],
            "facts": [{"source_id": sid, "quote": anchor,
                       "claim": f"材料背景：{anchor[:40]}"}],
            "conflicts": [],
            "gaps": [f"该问题的直接答案不在材料中：{c['question']}"],
            "forbidden_claims": [f"编造「{c['question'][:30]}」的直接答案",
                                 "虚构材料中不存在的具体数字"],
            "expected_outcome": "unable", "batch": "v3",
            "mechanisms": ["refuse_without_evidence"]})
    return sources, tasks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default=".tmp/v3_public")
    parser.add_argument("--out", default="eval/datasets/research_writing_v3_public.json")
    args = parser.parse_args()
    raw = ROOT / args.raw
    cmrc = load_cmrc(raw)
    dureader = load_dureader(raw)
    if len(cmrc) < CMRC_TARGET or len(dureader) < DUREADER_TARGET:
        raise SystemExit(f"可用样例不足：cmrc={len(cmrc)} dureader_unans={len(dureader)}")
    sources, tasks = build_sources_and_tasks(cmrc, dureader)
    dataset = {
        "meta": {
            "name": "research_writing_v3_public",
            "version": 3,
            "created": "2026-09-29",
            "data_policy": ("公开数据集转换：CMRC 2018（CC BY-SA 4.0，正文入库按同许可证共享、"
                            "注明出处）；DuReader_robust（仅供研究用途，对外分发前复核）。"
                            "v1 冻结分母与 v2 扩充不受影响；跨批次对比仍用 v1 子集。"),
            "execution_status": "not_run",
            "counts": {"research": CMRC_TARGET + DUREADER_TARGET, "fault": 0},
            "v1_baseline": {"frozen_ids": []},
            "extension_note": ("批次 A（docs/PUBLIC_DATASETS_CANDIDATES.md §四）：CMRC dev 20 例"
                               "（evidence_location）+ DuReader_robust unanswerable 10 例"
                               "（refuse_without_evidence）；确定性采样 seed="
                               f"{SEED}；quote 逐字命中已逐例断言。"),
            "provenance": {
                "cmrc2018": {"repo": "https://github.com/ymcui/cmrc2018",
                             "file": "data/cmrc2018_dev.json", "license": "CC BY-SA 4.0"},
                "dureader_robust": {"distribution": "https://dataset-bj.cdn.bcebos.com/"
                                                   "dureader_robust/data/dureader_robust-data.tar.gz",
                                    "file": "dev.json", "license": "研究用途（随包 License.docx）"}},
            "conversion": {"script": "scripts/build_v3_from_public.py", "seed": SEED,
                           "cmrc_pool": len(cmrc), "dureader_pool": len(dureader),
                           "raw_ids": {
                               **{t["id"]: c["query_id"] for t, c in
                                  zip([x for x in tasks if x["id"].startswith("pc")],
                                      cmrc[:CMRC_TARGET])},
                               **{t["id"]: c["raw_id"] for t, c in
                                  zip([x for x in tasks if x["id"].startswith("pu")],
                                      dureader[:DUREADER_TARGET])}},
                           },
        },
        "sources": sources,
        "tasks": tasks,
        "faults": [],
    }
    # 最终断言：quote 逐字命中 + id 唯一
    by_id = {s["id"]: s for s in sources}
    for t in tasks:
        for f in t["facts"]:
            assert f["quote"] in by_id[f["source_id"]]["text"], t["id"]
    assert len({t["id"] for t in tasks}) == len(tasks)
    out = ROOT / args.out
    with io.open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(dataset, ensure_ascii=False, indent=1) + "\n")
    print(f"written {out}：tasks={len(tasks)} sources={len(sources)}"
          f"（cmrc {min(CMRC_TARGET, len(cmrc))} + dureader_unans {min(DUREADER_TARGET, len(dureader))}）")


if __name__ == "__main__":
    main()
