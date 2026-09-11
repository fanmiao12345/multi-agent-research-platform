# -*- coding: utf-8 -*-
"""Q3：基线与候选业务报告同条件对比。"""
from __future__ import annotations
import argparse, json, statistics
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent

def _metrics(report):
    rows=report.get("records",[])
    accepted=sum(1 for r in rows if r.get("draft_level")=="accepted")
    costs=[float(r.get("estimated_cost_usd") or 0) for r in rows]
    lat=[float(r.get("elapsed_seconds") or 0) for r in rows]
    return {"attempts":len(rows),"accepted":accepted,
            "accept_rate":round(accepted/len(rows),4) if rows else None,
            "estimated_cost_usd":round(sum(costs),6),
            "mean_latency_seconds":round(statistics.mean(lat),4) if lat else None}

def compare(base,cand):
    b=_metrics(base); c=_metrics(cand)
    delta={k:(round(c[k]-b[k],6) if isinstance(b[k],(int,float)) and isinstance(c[k],(int,float)) else None)
           for k in b}
    return {"schema_version":1,"created_at":datetime.now().isoformat(timespec="seconds"),
            "baseline":b,"candidate":c,"delta":delta,
            "policy":"质量、费用、时延同时报告；无收益不得声明更优"}

def main():
    p=argparse.ArgumentParser();p.add_argument("--baseline",required=True);p.add_argument("--candidate",required=True);p.add_argument("--out",default="eval/reports/q3_compare.json");a=p.parse_args()
    result=compare(json.loads(Path(a.baseline).read_text(encoding="utf-8")),json.loads(Path(a.candidate).read_text(encoding="utf-8")))
    out=ROOT/a.out;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    out.with_suffix(".md").write_text("# Q3 对比\n\n"+json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=="__main__":main()