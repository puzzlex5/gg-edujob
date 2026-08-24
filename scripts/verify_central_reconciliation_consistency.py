#!/usr/bin/env python3
"""Fail closed when the two independent central-source audits are logically inconsistent.

`verify_central_pagination.py` proves the official central list pagination independently using the
portal's current active-oriented list semantics. `reconcile_source_ids.py` proves the broader
recent 90-day population used for completeness/recovery. Therefore the two populations should not
be forced to have identical counts: the active set is expected to be a subset of the 90-day set.
This checker requires the 90-day reconciliation count to be at least as large as the independently
traversed active count, in the same audit window. A smaller 90-day count is impossible and fails
closed.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CENTRAL = ROOT / "central_pagination_report.json"
RECON = ROOT / "source_reconciliation_report.json"
KST = timezone(timedelta(hours=9))
MAX_SKEW_MINUTES = 30


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_kst(value):
    return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S KST").replace(tzinfo=KST)


def main():
    central = load(CENTRAL)
    recon = load(RECON)
    if not central.get("complete"):
        raise SystemExit("Central pagination report is not complete")

    try:
        ctime = parse_kst(central.get("generatedAt"))
        rtime = parse_kst(recon.get("generatedAt"))
    except Exception as exc:
        raise SystemExit(f"Cannot prove audit timestamp consistency: {type(exc).__name__}")
    skew = abs((ctime - rtime).total_seconds()) / 60
    if skew > MAX_SKEW_MINUTES:
        raise SystemExit(f"Central/reconciliation reports are from different audit windows: {skew:.1f} minutes")

    recon_sources = {str(x.get("name") or ""): x for x in (recon.get("sources") or [])}
    mismatches = []
    checked = []
    for src in central.get("sources") or []:
        name = str(src.get("name") or "")
        other = recon_sources.get(name)
        if not other:
            mismatches.append({"name": name, "reason": "missing-from-reconciliation"})
            continue
        active_count = int(src.get("stableIdCount") or 0)
        recent_count = int(other.get("officialIdCount") or 0)
        item = {
            "name": name,
            "activeStableIds": active_count,
            "recent90DayOfficialIds": recent_count,
            "relation": "active-subset-of-recent",
        }
        checked.append(item)
        if recent_count < active_count:
            mismatches.append(item)

    if len(checked) != 2:
        raise SystemExit(f"Expected exactly two central sources, checked={checked}, mismatches={mismatches}")
    if mismatches:
        raise SystemExit("Central 90-day population is smaller than independently proven active population: " + json.dumps(mismatches, ensure_ascii=False))

    print(json.dumps({"state": "ok", "timestampSkewMinutes": round(skew, 2), "sources": checked}, ensure_ascii=False))


if __name__ == "__main__":
    main()
