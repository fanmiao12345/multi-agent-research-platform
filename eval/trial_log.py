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
    days=sorted({t.get("date") for t in data.get("tasks",[])})
    count=len(data.get("tasks",[]))
    return {"tasks":count,"days":len(days),"dates":days,
            "ready":count>=20 and len(days)>=7,
            "policy":"必须是真实个人任务；不能由自动批次冒充"}

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest="cmd",required=True)
    add=sub.add_parser("add");add.add_argument("--task",required=True);add.add_argument("--result",required=True);add.add_argument("--intervention-minutes",type=float,default=0);add.add_argument("--revision-minutes",type=float,default=0);add.add_argument("--status",default="completed");add.add_argument("--notes",default="");add.add_argument("--date",default=date.today().isoformat())
    sub.add_parser("status")
    args=p.parse_args();data=load()
    if args.cmd=="status":
        print(json.dumps(status(data),ensure_ascii=False,indent=2));return
    data["tasks"].append({"id":"trial_"+uuid.uuid4().hex[:8],"date":args.date,"task":args.task,"result":args.result,"status":args.status,"intervention_minutes":args.intervention_minutes,"revision_minutes":args.revision_minutes,"notes":args.notes,"recorded_at":datetime.now().isoformat(timespec="seconds")})
    save(data);print(json.dumps(status(data),ensure_ascii=False,indent=2))
if __name__=="__main__":main()