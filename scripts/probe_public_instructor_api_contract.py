#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

KST = timezone(timedelta(hours=9))
API = "https://prod-backend.00gangsa.com/v1/recruitments"
OUT = Path("public_instructor_api_contract_report.json")


def fetch(params: dict) -> dict:
    r = requests.get(API, params=params, timeout=30, headers={"Accept":"application/json","User-Agent":"MetroEduJob/1.0"})
    r.raise_for_status()
    data = r.json()
    d = data.get("data") or {}
    rows = d.get("list") or []
    return {
        "params": params,
        "url": r.url,
        "status": r.status_code,
        "count": d.get("count"),
        "totalPage": d.get("totalPage"),
        "currentPage": d.get("currentPage"),
        "hasNextPage": d.get("hasNextPage"),
        "ids": [str(x.get("id")) for x in rows if x.get("id") is not None],
        "states": sorted({str(x.get("state")) for x in rows}),
        "provinces": sorted({str((((reg or {}).get("province") or {}).get("name"))) for x in rows for reg in (x.get("regions") or []) if (((reg or {}).get("province") or {}).get("name"))}),
        "sample": rows[:2],
    }


def main() -> int:
    tests = []
    errors = []
    candidates = [
        {}, {"page":2}, {"page":3}, {"size":100}, {"limit":100},
        {"provinceId":1}, {"provinceId":2}, {"provinceIds":"1,2"},
        {"provinceIds":1}, {"provinceIds":2}, {"provinceIds[]":1}, {"provinceIds[]":2},
        {"state":"RECRUITING"}, {"states":"RECRUITING"},
        {"provinceId":1,"state":"RECRUITING"}, {"provinceId":2,"state":"RECRUITING"},
        {"provinceIds":"1,2","state":"RECRUITING"},
    ]
    for params in candidates:
        try:
            tests.append(fetch(params))
        except Exception as exc:
            errors.append({"params":params,"error":f"{type(exc).__name__}: {exc}"})

    baseline = next((x for x in tests if x["params"] == {}), None)
    p2 = next((x for x in tests if x["params"] == {"page":2}), None)
    page_works = bool(baseline and p2 and p2.get("currentPage") == 2 and set(baseline.get("ids") or []) != set(p2.get("ids") or []))

    filter_evidence = []
    for x in tests:
        params=x["params"]
        if not params or params in ({"page":2},{"page":3},{"size":100},{"limit":100}):
            continue
        changed_count = baseline and x.get("count") != baseline.get("count")
        narrowed_provinces = bool(x.get("provinces")) and set(x.get("provinces") or []).issubset({"서울","경기"})
        recruiting_only = x.get("states") == ["RECRUITING"]
        if changed_count or narrowed_provinces or recruiting_only:
            filter_evidence.append({"params":params,"count":x.get("count"),"totalPage":x.get("totalPage"),"provinces":x.get("provinces"),"states":x.get("states"),"url":x.get("url")})

    report={
        "generatedAt":datetime.now(KST).isoformat(timespec="seconds"),
        "source":"공공강사",
        "api":API,
        "mode":"read-only-api-contract-probe",
        "publicationEnabled":False,
        "tests":tests,
        "errors":errors,
        "summary":{
            "pageParamWorks":page_works,
            "filterEvidenceCount":len(filter_evidence),
            "readyForCollector":bool(page_works and filter_evidence and not errors),
        },
        "filterEvidence":filter_evidence,
        "policy":{"publishBeforeValidation":False,"nextGate":"isolated Seoul/Gyeonggi recruiting traversal + missingAfter=0"},
    }
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report["summary"],ensure_ascii=False))
    return 0 if tests else 2

if __name__ == "__main__":
    raise SystemExit(main())
