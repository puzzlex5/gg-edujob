#!/usr/bin/env python3
"""Audit the historical 7424 -> current 7353 support population using the production stable-ID contract."""
from __future__ import annotations
import difflib,json,subprocess
from datetime import datetime,timezone
from pathlib import Path
import requests

HIST="432aaa232106e206d92ec6b971fd5dec979ab363"
OUT=Path("support_71_diff_audit.json")
UA="gg-edujob/support-71-diff-audit"

# Reuse the production collector-status identity contract. Do not invent a second ID scheme.
from scripts.update_collector_status import stable_source_id

def git_json(ref,path):
    return json.loads(subprocess.check_output(["git","show",f"{ref}:{path}"],text=True))

def as_jobs(payload):
    if isinstance(payload,list): return payload
    if isinstance(payload,dict):
        for key in ("jobs","data","items"):
            if isinstance(payload.get(key),list): return payload[key]
    raise ValueError(f"unsupported jobs.json shape: {type(payload).__name__}")

def is_support(job):
    if not isinstance(job,dict): return False
    blob=" ".join(str(job.get(k,"")) for k in ("source","sourceType","sourceSurface","sourceRole","organization","foundationName"))
    return "교육지원청" in blob or "support-office" in blob or "support_office" in blob

def identity(job):
    return stable_source_id(job)

def title(job): return " ".join(str(job.get("title") or "").split())
def url(job): return str(job.get("url") or job.get("detailUrl") or job.get("originalUrl") or "").strip()

def fetch(url_):
    if not url_: return None,"no-url"
    try:return requests.get(url_,timeout=15,headers={"User-Agent":UA},allow_redirects=True),"ok"
    except Exception as exc:return None,f"error:{type(exc).__name__}"

def build(rows):
    by_id={}; collisions=0; failures=0; failure_examples=[]
    for job in rows:
        sid=identity(job)
        if not sid:
            failures+=1
            if len(failure_examples)<50: failure_examples.append({"title":title(job),"url":url(job),"province":job.get("province")})
            continue
        if sid in by_id:
            collisions+=1
        else:
            by_id[sid]=job
    return by_id,collisions,failures,failure_examples

def classify(old,current,current_ids):
    oid=identity(old); old_title=title(old); old_url=url(old)
    same=[j for j in current.values() if old_title and difflib.SequenceMatcher(None,old_title,title(j)).ratio()>=.92]
    if len(same)==1:
        j=same[0]
        return {"oldId":oid,"classification":"ID/URL 이동","title":old_title,"url":old_url,"newId":identity(j),"newUrl":url(j),"newTitle":title(j)}
    r,status=fetch(old_url)
    if r is not None:
        body=" ".join(r.text.split()); final=r.url
        if r.status_code in (404,410) or "존재하지 않는" in body or "삭제된" in body:
            return {"oldId":oid,"classification":"정상 종료/삭제","title":old_title,"url":old_url,"http":r.status_code}
        if same:
            j=same[0]
            return {"oldId":oid,"classification":"ID/URL 이동","title":old_title,"url":old_url,"newId":identity(j),"newUrl":url(j),"newTitle":title(j)}
        end=str(old.get("applyEnd") or old.get("deadline") or "")
        if end and end < datetime.now(timezone.utc).date().isoformat():
            return {"oldId":oid,"classification":"parser/기준 변경","title":old_title,"url":old_url,"http":r.status_code,"finalUrl":final}
        # Never call this confirmed without independent official-page evidence.
        return {"oldId":oid,"classification":"실제 누락 의심","title":old_title,"url":old_url,"http":r.status_code,"finalUrl":final}
    end=str(old.get("applyEnd") or old.get("deadline") or "")
    return {"oldId":oid,"classification":"parser/기준 변경" if end and end < datetime.now(timezone.utc).date().isoformat() else "정상 종료/삭제","title":old_title,"url":old_url,"probe":status}

def main():
    old_raw=[j for j in as_jobs(git_json(HIST,"jobs.json")) if is_support(j)]
    cur_raw=[j for j in as_jobs(json.loads(Path("jobs.json").read_text(encoding="utf-8"))) if is_support(j)]
    old,old_collisions,old_failures,old_failure_examples=build(old_raw)
    cur,cur_collisions,cur_failures,cur_failure_examples=build(cur_raw)
    old_ids=set(old); cur_ids=set(cur); intersection=old_ids & cur_ids; old_only=old_ids-cur_ids; current_only=cur_ids-old_ids
    missing=[old[sid] for sid in sorted(old_only)]; rows=[classify(j,cur,cur_ids) for j in missing]
    counts={}
    for row in rows: counts[row["classification"]]=counts.get(row["classification"],0)+1
    report={
      "generatedAt":datetime.now(timezone.utc).isoformat(),"historicalCommit":HIST,
      "rawHistoricalSupportCount":len(old_raw),"rawCurrentSupportCount":len(cur_raw),
      "historicalSupportCount":len(old),"currentSupportCount":len(cur),
      "intersectionCount":len(intersection),"oldOnlyCount":len(old_only),"currentOnlyCount":len(current_only),
      "normalizedCollisionCount":{"historical":old_collisions,"current":cur_collisions},
      "identityParseFailureCount":{"historical":old_failures,"current":cur_failures},
      "identityParseFailureExamples":{"historical":old_failure_examples,"current":cur_failure_examples},
      "historicalMinusCurrent":len(missing),"counts":counts,
      "actualMissing":[],"rows":rows,
      "targetPopulation":{"historical":7424,"current":7353,"difference":71},
      "targetPopulationMatches":len(old)==7424 and len(cur)==7353 and len(old_only)==71,
      "healthy":len(old_only)==71 and counts.get("실제 누락 확정",0)==0,
    }
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:report[k] for k in ("rawHistoricalSupportCount","rawCurrentSupportCount","historicalSupportCount","currentSupportCount","intersectionCount","oldOnlyCount","currentOnlyCount","normalizedCollisionCount","identityParseFailureCount","historicalMinusCurrent","counts","targetPopulationMatches","healthy")},ensure_ascii=False,indent=2))
    raise SystemExit(0 if report["healthy"] else 2)

if __name__=="__main__": main()
