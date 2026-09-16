# -*- coding: utf-8 -*-
"""Q2-03：真实业务/联网基线汇总。"""
from __future__ import annotations
import argparse, json, statistics
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/"eval"/"reports"/"q2_summary.json"


def _num(values):
    vals=[float(v) for v in values if isinstance(v,(int,float))]
    if not vals: return {"mean":None,"p50":None,"p95":None}
    vals=sorted(vals)
    def pct(p):
        idx=min(len(vals)-1,max(0,round((len(vals)-1)*p)))
        return vals[idx]
    return {"mean":round(statistics.mean(vals),4),"p50":round(pct(.5),4),"p95":round(pct(.95),4)}


def summarize(business: dict|None, web: dict|None, human: dict|None) -> dict:
    records=(business or {}).get("records",[])
    total_cost=sum(float(r.get("estimated_cost_usd") or 0) for r in records)
    unknown=sum(int(r.get("unknown_usage_calls") or 0) for r in records)
    citations=sum(int((r.get("machine_checks") or {}).get("citation_tokens") or 0) for r in records)
    unresolved=sum(int((r.get("machine_checks") or {}).get("unresolved_citations") or 0) for r in records)
    human_meta=(human or {}).get("meta",{}).get("human_confirm") if human else None
    web_rows=(web or {}).get("rows",[])
    result={
      "schema_version":1,"created_at":datetime.now().isoformat(timespec="seconds"),
      "business":{
        "attempts_total":len(records),
        "accepted":sum(1 for r in records if r.get("draft_level")=="accepted"),
        "draft":sum(1 for r in records if r.get("draft_level")=="draft"),
        "unable":sum(1 for r in records if r.get("draft_level")=="unable"),
        "failed":sum(1 for r in records if r.get("status")=="failed"),
        "not_executed":sum(1 for r in records if r.get("status")=="not_executed"),
        "estimated_cost_usd":round(total_cost,6),
        "unknown_usage_calls":unknown,
        "latency_seconds":_num([r.get("elapsed_seconds") for r in records]),
        "citations":citations,"unresolved_citations":unresolved,
      },
      "web":{
        "topics":len(web_rows),
        "successful":sum(1 for r in web_rows if r.get("returncode")==0),
        "mode_coverage":(web or {}).get("mode_coverage",{}),
        "usable_sources":sum(int(r.get("usable_sources") or 0) for r in web_rows),
        "lineage_links":sum(len(r.get("citation_lineage") or []) for r in web_rows),
      },
      "human_confirm":human_meta,
      "ready_for_q3":bool(human_meta and human_meta.get("graded_human",0)>0
                          and (web or {}).get("requirements_met",False)),
      "policy":"人工评分未确认时不能把均分或链内 accepted 当作业务验收通过"
    }
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    md=["# Q2 完整基线汇总","",json.dumps(result,ensure_ascii=False,indent=2)]
    (OUT.with_suffix(".md")).write_text("\n".join(md)+"\n",encoding="utf-8")
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--business",default=str(ROOT/"eval/reports/q2_real_batch/business_report.json"))
    parser.add_argument("--web",default=str(ROOT/"eval/reports/q2_web_baseline.json"))
    parser.add_argument("--human",default=None)
    args=parser.parse_args()
    def load(path):
        if not path:                      # 未提供该路输入（如联网/人工尚未执行）按缺失处理
            return None
        p=Path(path)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    result=summarize(load(args.business),load(args.web),load(args.human))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result["ready_for_q3"] else 1)


if __name__=="__main__":
    main()