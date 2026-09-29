# -*- coding: utf-8 -*-
"""
build_v3b_holdout.py —— 留出集：未见公开数据 → v3b 业务案例（泛化性对照用）

用途（响应"方法是否过拟合 v3"的怀疑）：两个规则设计时从未接触的数据源，
用于新旧提示词配对对照：
- DuReader_robust train answerable 6 例（中文、真实、同单段形态——隔离语言/形态因素）
- HotpotQA distractor validation 6 例（英文、每题 10 段多来源含 8 段干扰——
  压力测试"一句多引纪律"在多来源合法场景的表现；句子级 supporting facts 作 quote）

与 build_v3_from_public.py 同口径：quote 逐字命中断言、确定性采样、映射记录入 meta。
运行：.venv/Scripts/python scripts/build_v3b_holdout.py [--raw .tmp] [--out <路径>]
"""
from __future__ import annotations

import argparse
import io
import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = 20260929
MAX_CONTEXT_CHARS = 1600        # hotpot 单段较长，放宽到 1600
HOTPOT_TARGET = 6
DUREADER_TARGET = 6

_SYS = __import__("sys")
if str(ROOT / "scripts") not in _SYS.path:
    _SYS.path.insert(0, str(ROOT))
sys_path = _SYS.path


def _stable(key: str) -> int:
    return int(uuid.uuid5(uuid.NAMESPACE_URL, key).int % (2 ** 31))


def load_dureader_train(raw: Path) -> list[dict]:
    data = json.loads(io.open(raw / "v3_public" / "dureader_robust-data" / "train.json",
                              encoding="utf-8").read())["data"]
    cases = []
    for article in data:
        for para in article["paragraphs"]:
            context = para["context"]
            if not (200 <= len(context) <= MAX_CONTEXT_CHARS):
                continue
            for qa in para["qas"]:
                answers = [a.get("text") for a in (qa.get("answers") or [])
                           if isinstance(a.get("text"), str) and a.get("text").strip()]
                if len(set(answers)) != 1:
                    continue
                answer = answers[0]
                if not (4 <= len(answer) <= 60) or answer not in context:
                    continue
                question = qa["question"].strip()
                if question and any("\u4e00" <= ch <= "\u9fff" for ch in question):
                    cases.append({"raw_id": qa["id"], "question": question,
                                  "context": context, "answer": answer,
                                  "title": article.get("title", "")})
    cases.sort(key=lambda c: _stable("dureader_robust_train/" + c["raw_id"]))
    return cases


def load_hotpot(raw: Path) -> list[dict]:
    rows = json.loads(io.open(raw / "v3b_public" / "hotpot_rows.json",
                              encoding="utf-8").read())
    cases = []
    for row in rows:
        ctx = row["context"]
        titles, sentences = ctx["title"], ctx["sentences"]
        if len(titles) < 8:            # distractor 形态：多段含干扰
            continue
        sf_titles = (row.get("supporting_facts") or {}).get("title", [])
        sf_ids = (row.get("supporting_facts") or {}).get("sent_id", [])
        facts = []
        ok = True
        for title, sent_id in zip(sf_titles, sf_ids):
            if title not in titles:
                ok = False
                break
            para = sentences[titles.index(title)]
            if not isinstance(sent_id, int) or sent_id >= len(para):
                ok = False
                break
            sent = para[sent_id].strip()
            if not (8 <= len(sent) <= 300):
                ok = False
                break
            facts.append({"title": title, "quote": sent})
        if not ok or len(facts) < 2:
            continue
        question = row["question"].strip()
        if len(question) > 160:
            continue
        cases.append({"raw_id": row["id"], "question": question,
                      "titles": titles, "sentences": sentences, "facts": facts,
                      "answer": row.get("answer", ""), "type": row.get("type", "")})
    cases.sort(key=lambda c: _stable("hotpotqa/" + c["raw_id"]))
    return cases


def build(dureader: list[dict], hotpot: list[dict]) -> tuple[list, list]:
    sources, tasks = [], []
    for i, c in enumerate(dureader[:DUREADER_TARGET], start=1):
        sid, tid = f"s7{i:02d}", f"pd{i:02d}"
        sources.append({"id": sid, "title": f"DuReader-train {c['title'][:20]}",
                        "date": "unknown", "text": c["context"],
                        "origin": "public_dataset:dureader_robust_train",
                        "url": "https://github.com/PaddlePaddle/DuReader-robust/issues"})
        assert c["answer"] in c["context"]
        tasks.append({
            "id": tid, "category": "research", "title": c["question"][:30],
            "status": "not_run", "input_kind": "local", "source_ids": [sid],
            "request": f"根据材料回答问题，并给出可定位的依据：{c['question']}",
            "sections": ["结论", "依据与定位"],
            "facts": [{"source_id": sid, "quote": c["answer"], "claim": c["answer"]}],
            "conflicts": [], "gaps": [],
            "forbidden_claims": ["编造材料中不存在的数字或细节", "虚构出处、页码或作者"],
            "expected_outcome": "final", "batch": "v3b",
            "mechanisms": ["evidence_location"]})
    for i, c in enumerate(hotpot[:HOTPOT_TARGET], start=1):
        sid_base = 800 + i * 10
        sids = []
        for j, (title, sentences) in enumerate(zip(c["titles"], c["sentences"])):
            sid = f"s{sid_base + j:02d}"
            sids.append(sid)
            text = "".join(sentences)
            sources.append({"id": sid, "title": f"HotpotQA {title[:30]}",
                            "date": "unknown", "text": text,
                            "origin": "public_dataset:hotpotqa_distractor",
                            "url": "https://hotpotqa.github.io/"})
        quote_facts = []
        for f in c["facts"]:
            sid = sids[c["titles"].index(f["title"])]
            assert f["quote"] in next(s["text"] for s in sources if s["id"] == sid)
            quote_facts.append({"source_id": sid, "quote": f["quote"],
                                "claim": f["quote"][:60]})
        tasks.append({
            "id": f"ph{i:02d}", "category": "research",
            "title": c["question"][:30],
            "status": "not_run", "input_kind": "local", "source_ids": sids,
            "request": f"根据材料回答问题，并给出可定位的依据：{c['question']}",
            "sections": ["结论", "依据与定位"],
            "facts": quote_facts, "conflicts": [], "gaps": [],
            "forbidden_claims": ["编造材料中不存在的数字或细节", "虚构出处、页码或作者"],
            "expected_outcome": "final", "batch": "v3b",
            "mechanisms": ["evidence_location", "subtopic_decomposition"]})
    return sources, tasks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default=".tmp")
    parser.add_argument("--out", default="eval/datasets/research_writing_v3b_holdout.json")
    args = parser.parse_args()
    raw = ROOT / args.raw
    dureader = load_dureader_train(raw)
    hotpot = load_hotpot(raw)
    if len(dureader) < DUREADER_TARGET or len(hotpot) < HOTPOT_TARGET:
        raise SystemExit(f"样例不足：dureader={len(dureader)} hotpot={len(hotpot)}")
    sources, tasks = build(dureader, hotpot)
    dataset = {
        "meta": {
            "name": "research_writing_v3b_holdout",
            "version": 3,
            "created": "2026-09-29",
            "data_policy": ("留出集（泛化性对照）：两个规则设计时未接触的数据源。"
                            "HotpotQA CC BY-SA 4.0；DuReader_robust 仅供研究用途。"
                            "v1 冻结分母与 v2/v3 均不受影响。"),
            "execution_status": "not_run",
            "counts": {"research": DUREADER_TARGET + HOTPOT_TARGET, "fault": 0},
            "v1_baseline": {"frozen_ids": []},
            "extension_note": (f"确定性采样 seed={SEED}；HotpotQA 每例 10 段含干扰段，"
                               "句子级 supporting facts 作 quote；quote 逐字命中已逐例断言。"),
            "provenance": {
                "dureader_robust_train": {"file": "train.json",
                                          "license": "研究用途（随包 License.docx）"},
                "hotpotqa": {"distribution": "HF datasets-server hotpotqa/hotpot_qa distractor validation",
                             "license": "CC BY-SA 4.0"}},
            "conversion": {"script": "scripts/build_v3b_holdout.py", "seed": SEED,
                           "pools": {"dureader_train": len(dureader),
                                     "hotpot": len(hotpot)},
                           "raw_ids": {
                               **{t["id"]: c["raw_id"] for t, c in
                                  zip([x for x in tasks if x["id"].startswith("pd")],
                                      dureader[:DUREADER_TARGET])},
                               **{t["id"]: c["raw_id"] for t, c in
                                  zip([x for x in tasks if x["id"].startswith("ph")],
                                      hotpot[:HOTPOT_TARGET])}},
                           },
        },
        "sources": sources,
        "tasks": tasks,
        "faults": [],
    }
    by_id = {s["id"]: s for s in sources}
    for t in tasks:
        for f in t["facts"]:
            assert f["quote"] in by_id[f["source_id"]]["text"], t["id"]
    assert len({t["id"] for t in tasks}) == len(tasks)
    out = ROOT / args.out
    with io.open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(dataset, ensure_ascii=False, indent=1) + "\n")
    print(f"written {out}：tasks={len(tasks)} sources={len(sources)}"
          f"（dureader_train {DUREADER_TARGET} + hotpot {HOTPOT_TARGET}）")


if __name__ == "__main__":
    main()
