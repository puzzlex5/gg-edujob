#!/usr/bin/env python3
"""Single production contract for the support-office population used by collection and audit."""
from __future__ import annotations
import json, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
KST=timezone(timedelta(hours=9)); LOOKBACK_DAYS=90
JOB_WORDS=re.compile(r"채용|구인|모집|기간제|계약제|시간강사|강사|교사|교원|공무직|사무직|근로자|조리|돌봄|보육|봉사|튜터|안전지킴이|외부강사")
EXCLUDE_WORDS=re.compile(r"최종\s*합격|합격자|서류\s*심사|서류전형|면접\s*대상|선정\s*결과|채용\s*결과|전형\s*결과|합격\s*공고|인사발령")
DATE_RE=re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")

def support_office_names():
    data=json.loads((Path(__file__).resolve().parents[1]/"sources.json").read_text(encoding="utf-8"))
    return {str(x.get("name") or "").strip() for p in ("gyeonggi","seoul") for x in data.get(p,{}).get("supportOffices",[]) if str(x.get("name") or "").strip()}

SUPPORT_OFFICE_NAMES=support_office_names()

def parse_registered(value):
    m=DATE_RE.search(str(value or ""))
    if not m: return None
    try: return datetime(int(m.group(1)),int(m.group(2)),int(m.group(3)),tzinfo=KST)
    except ValueError: return None

def is_support_population_job(job, *, as_of=None):
    if not isinstance(job,dict): return False
    if str(job.get("source") or "").strip() not in SUPPORT_OFFICE_NAMES: return False
    if str(job.get("province") or "").strip() not in {"경기","서울"}: return False
    title=re.sub(r"\s+"," ",str(job.get("title") or "")).strip()
    if EXCLUDE_WORDS.search(title) or not JOB_WORDS.search(title): return False
    registered=parse_registered(job.get("registered"))
    if registered is None: return True
    now=as_of or datetime.now(KST)
    return registered >= now-timedelta(days=LOOKBACK_DAYS) and registered <= now

def contract_metadata(*, as_of=None):
    now=as_of or datetime.now(KST)
    return {"lookbackDays":LOOKBACK_DAYS,"asOf":now.isoformat(),"provinces":["경기","서울"],"supportOfficeCount":len(SUPPORT_OFFICE_NAMES),"supportOfficeNames":sorted(SUPPORT_OFFICE_NAMES),"sourceOfTruth":"sources.json::gyeonggi.supportOffices + sources.json::seoul.supportOffices","dataset":"jobs.json","filter":"source exact support-office name + province in {경기,서울} + production JOB_WORDS + production EXCLUDE_WORDS + 90-day registered window"}
