#!/usr/bin/env python3
"""Detect official sources whose newest observed registration is already stale.

This complements rolling 1d/7d/30d comparisons: a freshly deployed/reset history must
not wait seven days before surfacing a source that has already stopped receiving jobs.
Warnings are observational and do not mutate or block canonical publication.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
INPUT = Path("official_source_trend_report.json")
OUTPUT = Path("official_source_freshness_report.json")
STALE_DAYS = 7


def load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def main() -> int:
    now = datetime.now(KST)
    report = load(INPUT)
    sources = report.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("official source trend report has no source map")

    expected = int(report.get("registrySourceCount") or 0)
    observed = int(report.get("observedSourceCount") or 0)
    if expected <= 0 or observed != expected or len(sources) != expected:
        raise ValueError("official freshness check requires an exact registry source set")

    warnings: list[dict[str, Any]] = []
    for name, row in sources.items():
        if not isinstance(row, dict):
            continue
        latest = str(row.get("latestRegistered") or "")
        stable_count = int(row.get("stableIdCount") or 0)
        if not latest:
            if stable_count > 0:
                warnings.append({
                    "code": "official-source-freshness-unknown",
                    "source": name,
                    "stableIdCount": stable_count,
                })
            continue
        try:
            latest_date = datetime.fromisoformat(latest).date()
        except ValueError:
            warnings.append({
                "code": "official-source-invalid-latest-date",
                "source": name,
                "latestRegistered": latest,
            })
            continue
        age_days = (now.date() - latest_date).days
        if age_days >= STALE_DAYS:
            warnings.append({
                "code": "official-source-freshness-stall",
                "source": name,
                "latestRegistered": latest,
                "ageDays": age_days,
                "stableIdCount": stable_count,
                "baselineRequired": False,
            })

    output = {
        "generatedAt": now.isoformat(timespec="seconds"),
        "registrySourceCount": expected,
        "observedSourceCount": observed,
        "staleAfterDays": STALE_DAYS,
        "warningCount": len(warnings),
        "warnings": warnings,
        "policy": "surface already-stale official sources immediately; rolling baselines remain independent corroboration",
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sources": observed, "warnings": len(warnings)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
