#!/usr/bin/env python3
"""Canonical support-office dataset population contract shared by audit and evidence."""
from __future__ import annotations
import json
from pathlib import Path

PROVINCES={"경기","서울"}
DATASET="jobs.json"

def support_office_names():
    data=json.loads((Path(__file__).resolve().parents[1]/"sources.json").read_text(encoding="utf-8"))
    return {str(x.get("name") or "").strip() for p in ("gyeonggi","seoul") for x in data.get(p,{}).get("supportOffices",[]) if str(x.get("name") or "").strip()}

SUPPORT_OFFICE_NAMES=support_office_names()

def is_support_population_job(job, *, as_of=None):
    """Exact baseline predicate: canonical support-office source + metro province only.

    No title keyword, status, URL, or additional date predicate is allowed here.
    The temporal boundary is the jobs.json snapshot at each comparison ref.
    """
    if not isinstance(job,dict): return False
    return (str(job.get("source") or "").strip() in SUPPORT_OFFICE_NAMES and
            str(job.get("province") or "").strip() in PROVINCES)

def contract_metadata(*, as_of=None):
    return {"dataset":DATASET,"provinces":sorted(PROVINCES),"supportOfficeCount":len(SUPPORT_OFFICE_NAMES),"supportOfficeNames":sorted(SUPPORT_OFFICE_NAMES),"sourceOfTruth":"sources.json::gyeonggi.supportOffices + sources.json::seoul.supportOffices","temporalBoundary":"jobs.json snapshot at each comparison ref; no additional date/title/status filter","filter":"source exact support-office name + province in {경기,서울}"}
