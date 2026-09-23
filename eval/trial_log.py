# -*- coding: utf-8 -*-
"""Q4-01：连续 7 天真实试用日志。"""
from __future__ import annotations
import argparse,json,uuid
from datetime import date,datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
PATH=ROOT/"eval"/"reports"/"q4_trial"/"trial_log.json"

def load():
    if not PATH.exists(): return {"schema_version":1,"tasks":[]}
    return json.loads(PATH.read_text(encoding="utf-8"))

def save(data):
    PATH.parent.mkdir(parents=True,exist_ok=True)
    PATH.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")

def status(data):
    """验收口径（2026-09-23 P0-1）：达标只计 kind=plan 的计划任务；诊断/修复验证
    记录（kind=diagnostic）单独计数，不凑「20 任务/7 天」。缺 kind 的存量记录按 plan。"""
    tasks=data.get("tasks",[])
    plan=[t for t in tasks if t.get("kind","plan")=="plan"]
    plan_days=sorted({t.get("date") for t in plan})
    days=sorted({t.get("date") for t in tasks})
    return {"tasks":len(plan),"days":len(plan_days),"dates":plan_days,
            "diagnostic_tasks":len(tasks)-len(plan),
            "all_tasks":len(tasks),"all_days":len(days),
            "ready":len(plan)>=20 and len(plan_days)>=7,
            "policy":"必须是真实个人任务；不能由自动批次冒充；验收口径只计计划任务（诊断记录剔除）"}

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest="cmd",required=True)
    add=sub.add_parser("add");add.add_argument("--task",required=True);add.add_argument("--result",required=True);add.add_argument("--intervention-minutes",type=float,default=0);add.add_argument("--revision-minutes",type=float,default=0);add.add_argument("--status",default="completed");add.add_argument("--notes",default="");add.add_argument("--date",default=date.today().isoformat());add.add_argument("--kind",choices=("plan","diagnostic"),default="plan")
    sub.add_parser("status")
    args=p.parse_args();data=load()
    if args.cmd=="status":
        print(json.dumps(status(data),ensure_ascii=False,indent=2));return
    data["tasks"].append({"id":"trial_"+uuid.uuid4().hex[:8],"date":args.date,"kind":args.kind,"task":args.task,"result":args.result,"status":args.status,"intervention_minutes":args.intervention_minutes,"revision_minutes":args.revision_minutes,"notes":args.notes,"recorded_at":datetime.now().isoformat(timespec="seconds")})
    save(data);print(json.dumps(status(data),ensure_ascii=False,indent=2))
if __name__=="__main__":main()