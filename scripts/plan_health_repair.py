#!/usr/bin/env python3
"""Select at most one safe repair from health_anomaly_report.json with cooldowns."""
from __future__ import annotations
import argparse, json
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST=timezone(timedelta(hours=9))
MAP={
 'official-reconciliation-failed':('recover-missing-jobs.yml',140,4),
 'lessoninfo-integrity-failed':('update-lessoninfo-jobs.yml',135,3),
 'unified-search-integrity-failed':('unified-search.yml',130,3),
 'official-dataset-sudden-drop':('daily-deep-audit.yml',95,8),
 'official-source-zero-drop':('daily-deep-audit.yml',90,8),
 'official-source-freshness-stall':('daily-deep-audit.yml',70,12),
 'lessoninfo-sudden-drop':('update-lessoninfo-jobs.yml',90,8),
 'unified-sudden-drop':('unified-search.yml',85,8),
}

def load(p,default):
 try:return json.loads(Path(p).read_text(encoding='utf-8'))
 except Exception:return default

def now():return datetime.now(KST)
def dt(s):
 try:
  x=datetime.fromisoformat(str(s).replace('Z','+00:00'))
  return (x if x.tzinfo else x.replace(tzinfo=KST)).astimezone(KST)
 except Exception:return None

def main():
 ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
 p=sub.add_parser('plan');p.add_argument('--report',default='health_anomaly_report.json');p.add_argument('--state',default='health_repair_state.json');p.add_argument('--output',default='health_repair_plan.json')
 r=sub.add_parser('record');r.add_argument('--state',default='health_repair_state.json');r.add_argument('--plan',default='health_repair_plan.json');r.add_argument('--result',required=True)
 a=ap.parse_args(); n=now()
 if a.cmd=='plan':
  report=load(a.report,{}); state=load(a.state,{'history':{}}); hist=state.get('history',{})
  candidates=[]
  for x in report.get('anomalies',[]):
   code=x.get('code'); m=MAP.get(code)
   if not m:continue
   wf,priority,cool=m; last=dt((hist.get(code) or {}).get('lastAttemptAt'))
   blocked=bool(last and (n-last).total_seconds()<cool*3600)
   candidates.append({'code':code,'workflow':wf,'priority':priority,'cooldownHours':cool,'severity':x.get('severity'),'reason':x.get('message'),'blockedByCooldown':blocked})
  candidates.sort(key=lambda x:(-x['priority'],x['code']))
  selected=next((x for x in candidates if not x['blockedByCooldown']),None)
  out={'generatedAt':n.isoformat(timespec='seconds'),'reportStatus':report.get('status','missing'),'candidates':candidates,'selectedAction':selected}
  Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(selected,ensure_ascii=False));return 0
 plan=load(a.plan,{}); state=load(a.state,{'version':1,'history':{}}); sel=plan.get('selectedAction') or {}
 if sel:
  state.setdefault('history',{})[sel['code']]={'lastAttemptAt':n.isoformat(timespec='seconds'),'workflow':sel.get('workflow'),'result':a.result,'reason':sel.get('reason')}
  state['lastUpdatedAt']=n.isoformat(timespec='seconds')
  Path(a.state).write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 return 0
if __name__=='__main__': raise SystemExit(main())
