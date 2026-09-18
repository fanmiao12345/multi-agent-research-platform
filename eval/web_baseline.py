# -*- coding: utf-8 -*-
"""Q2-02：真实联网主题与六模式覆盖基线。"""
from __future__ import annotations
import argparse, json, time
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
DATASET=ROOT/"eval"/"datasets"/"web_topics_v1.json"
OUT=ROOT/"eval"/"reports"/"q2_web_baseline.json"


def run(max_cost: float, topic_filter: str | None = None,
        out_path: Path | None = None) -> dict:
    import subprocess, sys
    from config.settings import Settings
    settings=Settings()
    if not settings.search_provider:
        raise ValueError("SEARCH_PROVIDER 未配置，不能执行真实联网基线")
    cases=json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
    out=out_path or OUT
    workspace=out.parent/(out.stem+"_workspace")
    rows=[]
    import os as _os

    def write_report() -> dict:
        # 覆盖口径（runbook：六种方式各至少 2 个"适用案例"）：
        # 只要该题真的执行过（拿到 root_job_id）就计入覆盖；交付等级另计，
        # 不把"跑了但交付草稿/无法完成"当成没跑（CLI 对非 success 返回码即 1）。
        coverage={}
        accepted={}
        for row in rows:
            if row.get("root_job_id"):
                coverage[row["mode"]]=coverage.get(row["mode"],0)+1
                if row.get("returncode")==0:
                    accepted[row["mode"]]=accepted.get(row["mode"],0)+1
        report={"schema_version":1,"created_at":datetime.now().isoformat(timespec="seconds"),
                "batch_max_cost_per_task":max_cost,"rows":rows,"mode_coverage":coverage,
                "mode_accepted":accepted,
                "coverage_note":"coverage=实际执行过（有 root_job_id）的主题数；"
                                "mode_accepted=其中交付 accepted 的数量；"
                                "未执行/异常不计入覆盖",
                "requirements_met":all(coverage.get(m,0)>=2 for m in
                                       ("single","fixed","manager_worker","fanout",
                                        "dynamic_team","debate")),
                "nested_note":"二层嵌套由 D6/Q1 证据覆盖；本联网批次不把模式名称当嵌套成功"}
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        return report

    for case in cases:
        if topic_filter and case["id"] != topic_filter:
            continue
        started=time.time()
        command=[sys.executable,"-m","src.interfaces.cli",case["topic"],
                 "--mode","real","--flow","research",
                 "--orchestration",case["mode"],"--allow-network",
                 # 限额必须与业务批（eval/business_eval.py）一致，否则研究链跑到一半
                 # 就被 CLI 默认（12 次调用 / 8192 输出 token / 300 秒）掐断，
                 # 得到的是"没跑完"的假基线（2026-09-16 联网批首次运行即为此故障）。
                 "--max-calls","60","--max-output-tokens","200000","--max-seconds","1500",
                 "--max-cost",str(max_cost),"--workspace",str(workspace)]
        # 子进程中文输出在 Windows 下可能不是 UTF-8（GBK），必须容错解码，
        # 否则 subprocess 的读取线程会抛 UnicodeDecodeError 并丢掉整个 stdout。
        env=dict(_os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        row={"id":case["id"],"topic":case["topic"],"mode":case["mode"],
             "returncode":-1,"root_job_id":"","draft_level":None,
             "termination_reason":"not_started","elapsed_seconds":0.0,
             "source_count":0,"usable_sources":0,"citation_lineage":[],"message":""}
        try:
            proc=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,
                                encoding="utf-8",errors="replace",timeout=1800,env=env)
            try:
                payload=json.loads((proc.stdout or "").strip())
            except Exception:
                payload={}
            root_id=payload.get("root_job_id","")
            job_dir=workspace/"jobs"/root_id if root_id else None
            sources=[]
            if job_dir and (job_dir/"sources.json").exists():
                sources=json.loads((job_dir/"sources.json").read_text(encoding="utf-8")).get("sources",[])
            lineage={}
            if job_dir and (job_dir/"citation_lineage.json").exists():
                lineage=json.loads((job_dir/"citation_lineage.json").read_text(encoding="utf-8"))
            row.update({"returncode":proc.returncode,"root_job_id":root_id,
                        "draft_level":payload.get("draft_level"),
                        "termination_reason":payload.get("termination_reason"),
                        "source_count":len(sources),
                        "usable_sources":sum(1 for s in sources
                                             if s.get("status") in ("ok","partial")),
                        "citation_lineage":lineage.get("lineage",[]),
                        "message":payload.get("message",(proc.stderr or "")[-300:])})
        except Exception as e:  # noqa: BLE001 —— 单题失败不拖垮整批，如实记录
            row.update({"termination_reason":"exception",
                        "message":f"{type(e).__name__}: {str(e)[:200]}"})
        row["elapsed_seconds"]=round(time.time()-started,3)
        rows.append(row)
        write_report()          # 每题后落盘：中途崩溃不丢已完成结果
        print(f"[{case['id']}] {case['mode']} rc={row['returncode']} "
              f"level={row['draft_level']} sources={row['usable_sources']}/{row['source_count']} "
              f"{row['elapsed_seconds']}s", flush=True)
    return write_report()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--max-cost",type=float,required=True,
                        help="每个真实联网任务的费用上限（美元）")
    parser.add_argument("--topic",default=None)
    parser.add_argument("--out",default=None,
                        help="报告输出路径（默认 eval/reports/q2_web_baseline.json）；"
                             "工作台目录随之派生，避免覆盖既有基线")
    args=parser.parse_args()
    out_path=Path(args.out) if args.out else None
    report=run(args.max_cost,args.topic,out_path)
    print(json.dumps({"out":str(out_path or OUT),"rows":len(report["rows"]),
                      "coverage":report["mode_coverage"],
                      "requirements_met":report["requirements_met"]},
                     ensure_ascii=False,indent=2))
    raise SystemExit(0 if report["requirements_met"] else 1)


if __name__=="__main__":
    main()