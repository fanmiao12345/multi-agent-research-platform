# -*- coding: utf-8 -*-
"""Q2-02：真实联网主题与六模式覆盖基线。"""
from __future__ import annotations
import argparse, json, time
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
DATASET=ROOT/"eval"/"datasets"/"web_topics_v1.json"
OUT=ROOT/"eval"/"reports"/"q2_web_baseline.json"


def run(max_cost: float, topic_filter: str | None = None) -> dict:
    import subprocess, sys
    from config.settings import Settings
    settings=Settings()
    if not settings.search_provider:
        raise ValueError("SEARCH_PROVIDER 未配置，不能执行真实联网基线")
    cases=json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
    workspace=OUT.parent/"q2_web_workspace"
    rows=[]
    for case in cases:
        if topic_filter and case["id"] != topic_filter:
            continue
        started=time.time()
        command=[sys.executable,"-m","src.interfaces.cli",case["topic"],
                 "--mode","real","--flow","research",
                 "--orchestration",case["mode"],"--allow-network",
                 "--max-cost",str(max_cost),"--workspace",str(workspace)]
        proc=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,
                            encoding="utf-8",timeout=1800)
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
        rows.append({"id":case["id"],"topic":case["topic"],"mode":case["mode"],
                     "returncode":proc.returncode,"root_job_id":root_id,
                     "draft_level":payload.get("draft_level"),
                     "termination_reason":payload.get("termination_reason"),
                     "elapsed_seconds":round(time.time()-started,3),
                     "source_count":len(sources),
                     "usable_sources":sum(1 for s in sources if s.get("status") in ("ok","partial")),
                     "citation_lineage":lineage.get("lineage",[]),
                     "message":payload.get("message",(proc.stderr or "")[-300:])})
    coverage={}
    for row in rows:
        if row.get("returncode")==0:
            coverage[row["mode"]]=coverage.get(row["mode"],0)+1
    report={"schema_version":1,"created_at":datetime.now().isoformat(timespec="seconds"),
            "batch_max_cost_per_task":max_cost,"rows":rows,"mode_coverage":coverage,
            "requirements_met":all(coverage.get(m,0)>=2 for m in
                                   ("single","fixed","manager_worker","fanout","dynamic_team","debate")),
            "nested_note":"二层嵌套由 D6/Q1 证据覆盖；本联网批次不把模式名称当嵌套成功"}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--max-cost",type=float,required=True,
                        help="每个真实联网任务的费用上限（美元）")
    parser.add_argument("--topic",default=None)
    args=parser.parse_args()
    report=run(args.max_cost,args.topic)
    print(json.dumps({"out":str(OUT),"rows":len(report["rows"]),
                      "coverage":report["mode_coverage"],
                      "requirements_met":report["requirements_met"]},
                     ensure_ascii=False,indent=2))
    raise SystemExit(0 if report["requirements_met"] else 1)


if __name__=="__main__":
    main()