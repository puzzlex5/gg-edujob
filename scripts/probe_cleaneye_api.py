#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

KST = timezone(timedelta(hours=9))
BASE = "https://job.cleaneye.go.kr"
PAGE = BASE + "/user/ypRecruitment.do"
API = BASE + "/user/selectYpRecruitment.do"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36"


def compact(value, depth=0):
    if depth > 4:
        return "<depth>"
    if isinstance(value, dict):
        return {str(k): compact(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [compact(v, depth + 1) for v in value[:3]]
    s = str(value)
    return s[:500]


def main() -> int:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "ko-KR,ko;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": PAGE,
    })
    s.get(PAGE, timeout=30).raise_for_status()
    trials = [
        ("empty", {}),
        ("common-defaults", {
            "pageIndex": "1",
            "pageUnit": "10",
            "pageSize": "10",
            "status": "",
            "entName": "",
            "searchKeyword": "",
        }),
        ("seoul-foundation", {
            "pageIndex": "1",
            "pageUnit": "10",
            "pageSize": "10",
            "status": "",
            "entName": "서울문화재단",
            "searchKeyword": "서울문화재단",
        }),
    ]
    report = {"generatedAt": datetime.now(KST).isoformat(timespec="seconds"), "trials": []}
    for name, data in trials:
        item = {"name": name, "request": data}
        try:
            r = s.post(API, data=data, timeout=30)
            item.update({
                "status": r.status_code,
                "contentType": r.headers.get("content-type", ""),
                "textPrefix": r.text[:1000],
            })
            try:
                parsed = r.json()
                item["json"] = compact(parsed)
                if isinstance(parsed, dict):
                    item["keys"] = list(parsed.keys())
                    for k, v in parsed.items():
                        if isinstance(v, list):
                            item.setdefault("listLengths", {})[k] = len(v)
            except Exception as exc:
                item["jsonError"] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        report["trials"].append(item)
    Path("cleaneye_api_probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
