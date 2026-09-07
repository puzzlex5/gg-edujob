#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

from cultural_foundation_common import alias_index, generated_at, match_foundation

IN = Path("artmore_jobs.json")
OUT = Path("artmore_foundation_jobs.json")
REPORT = Path("artmore_foundation_report.json")


def rows(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("jobs", [])
    return []


def main() -> int:
    errors = []
    try:
        source_rows = rows(json.loads(IN.read_text(encoding="utf-8")))
    except Exception as exc:
        source_rows = []
        errors.append(f"{type(exc).__name__}: {exc}")
    pairs = alias_index()
    today = date.today().isoformat()
    jobs = []
    for row in source_rows:
        end = str(row.get("applyEnd") or "")
        if end and end < today:
            continue
        inst = match_foundation(" ".join(str(row.get(k) or "") for k in ("title", "rawListText", "source")), pairs)
        if not inst:
            continue
        jobs.append({
            "sourceIdentity": str(row.get("sourceIdentity") or ""),
            "source": "아트모아",
            "sourceRole": "cross-check",
            "foundationRegistryId": inst["id"],
            "foundationName": inst["name"],
            "region": inst["region"],
            "municipality": inst["municipality"],
            "title": str(row.get("title") or ""),
            "registered": str(row.get("registered") or ""),
            "applyEnd": end,
            "auditUrl": str(row.get("originalUrl") or row.get("url") or ""),
        })
    counts = Counter(x["foundationRegistryId"] for x in jobs)
    payload = {"generatedAt": generated_at(), "source": "아트모아", "role": "cross-check", "jobs": jobs}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    report = {
        "generatedAt": generated_at(),
        "healthy": not errors,
        "jobs": len(jobs),
        "institutionsSeen": len(counts),
        "byInstitution": dict(sorted(counts.items())),
        "errors": errors,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
