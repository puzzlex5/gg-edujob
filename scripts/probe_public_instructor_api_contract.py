#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

KST = timezone(timedelta(hours=9))
API = "https://prod-backend.00gangsa.com/v1/recruitments"
OUT = Path("public_instructor_api_contract_report.json")
BASE_BODY = {"page": 1, "size": 10, "order": "RECENT"}


def fetch(body: dict) -> dict:
    r = requests.post(
        API,
        json=body,
        timeout=30,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": "MetroEduJob/1.0",
            "Origin": "https://00gangsa.com",
            "Referer": "https://00gangsa.com/",
        },
    )
    r.raise_for_status()
    payload = r.json()
    d = payload.get("data") or {}
    rows = d.get("list") or []
    return {
        "body": body,
        "status": r.status_code,
        "count": d.get("count"),
        "totalPage": d.get("totalPage"),
        "currentPage": d.get("currentPage"),
        "hasPrevPage": d.get("hasPrevPage"),
        "hasNextPage": d.get("hasNextPage"),
        "listCount": len(rows),
        "ids": [str(x.get("id")) for x in rows if x.get("id") is not None],
        "states": sorted({str(x.get("state")) for x in rows}),
        "provinces": sorted({
            str((((reg or {}).get("province") or {}).get("name")))
            for x in rows
            for reg in (x.get("regions") or [])
            if (((reg or {}).get("province") or {}).get("name"))
        }),
        "sample": rows[:2],
    }


def main() -> int:
    tests = []
    errors = []
    candidates = [
        dict(BASE_BODY),
        {**BASE_BODY, "page": 2},
        {**BASE_BODY, "page": 3},
        {**BASE_BODY, "size": 100},
    ]
    for body in candidates:
        try:
            tests.append(fetch(body))
        except Exception as exc:
            errors.append({"body": body, "error": f"{type(exc).__name__}: {exc}"})

    baseline = next((x for x in tests if x["body"] == BASE_BODY), None)
    p2 = next((x for x in tests if x["body"].get("page") == 2), None)
    p3 = next((x for x in tests if x["body"].get("page") == 3), None)
    large = next((x for x in tests if x["body"].get("size") == 100), None)

    page_works = bool(
        baseline
        and p2
        and p3
        and p2.get("currentPage") == 2
        and p3.get("currentPage") == 3
        and set(baseline.get("ids") or []) != set(p2.get("ids") or [])
        and set(p2.get("ids") or []) != set(p3.get("ids") or [])
    )
    size_works = bool(
        large
        and large.get("listCount", 0) > (baseline or {}).get("listCount", 0)
        and large.get("currentPage") == 1
    )
    metadata_consistent = bool(
        baseline
        and isinstance(baseline.get("count"), int)
        and isinstance(baseline.get("totalPage"), int)
        and baseline.get("count", 0) > 0
        and baseline.get("totalPage", 0) > 0
    )

    report = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "공공강사",
        "api": API,
        "method": "POST",
        "mode": "read-only-api-contract-probe",
        "publicationEnabled": False,
        "tests": tests,
        "errors": errors,
        "summary": {
            "pageParamWorks": page_works,
            "sizeParamWorks": size_works,
            "metadataConsistent": metadata_consistent,
            "readyForCollector": bool(page_works and size_works and metadata_consistent and not errors),
        },
        "collectorContract": {
            "bodyTemplate": {"page": "<n>", "size": 100, "order": "RECENT"},
            "stableId": "gonggonggangsa:<row.id>",
            "detailUrl": "https://00gangsa.com/recruitments/<row.id>",
            "localCurrentMetroFilter": "row.state == RECRUITING and any(row.regions[].province.name in {서울, 경기}) and deadlineAt not expired",
        },
        "policy": {
            "publishBeforeValidation": False,
            "nextGate": "exhaustive POST traversal + independent dataset/ledger/state/report + missingAfter=0",
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"]["readyForCollector"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
