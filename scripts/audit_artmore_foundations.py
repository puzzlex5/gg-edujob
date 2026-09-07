#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
REGISTRY = Path("cultural_foundation_registry.json")
ARTMORE = Path("artmore_jobs.json")
OUT = Path("artmore_foundation_crosscheck.json")
REPORT = Path("artmore_foundation_crosscheck_report.json")


def norm(s: str) -> str:
    s = re.sub(r"\(\s*재\s*\)|재단법인", "", str(s or ""), flags=re.I)
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", s).lower()


def main() -> int:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    foundations = [r for r in registry.get("institutions", []) if r.get("enabled") is not False]
    aliases = []
    for f in foundations:
        for raw in [f.get("name"), *(f.get("aliases") or [])]:
            n = norm(raw)
            if n:
                aliases.append((n, f))
    aliases.sort(key=lambda x: len(x[0]), reverse=True)

    data = json.loads(ARTMORE.read_text(encoding="utf-8"))
    jobs = data if isinstance(data, list) else data.get("jobs", [])
    matches = []
    for j in jobs:
        hay = norm(" ".join(str(j.get(k) or "") for k in ("title", "rawListText", "school", "organization")))
        match = next((f for alias, f in aliases if alias in hay), None)
        if not match:
            continue
        matches.append({
            "sourceIdentity": j.get("sourceIdentity"),
            "foundationRegistryId": match["id"],
            "foundationName": match["name"],
            "region": match["region"],
            "municipality": match["municipality"],
            "title": j.get("title"),
            "registered": j.get("registered"),
            "applyEnd": j.get("applyEnd"),
            "url": j.get("url") or j.get("originalUrl"),
        })

    generated = datetime.now(KST).isoformat(timespec="seconds")
    institution_ids = sorted({m["foundationRegistryId"] for m in matches})
    OUT.write_text(json.dumps({"generatedAt": generated, "source": "아트모아", "jobs": matches}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    report = {
        "generatedAt": generated,
        "healthy": bool(jobs),
        "artmoreJobs": len(jobs),
        "foundationJobs": len(matches),
        "institutionsSeen": len(institution_ids),
        "institutionIds": institution_ids,
        "byRegion": dict(Counter(m["region"] for m in matches)),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if jobs else 2


if __name__ == "__main__":
    raise SystemExit(main())
