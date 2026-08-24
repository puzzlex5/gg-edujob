#!/usr/bin/env python3
"""Fail closed when the two independent central-source audits disagree.

`verify_central_pagination.py` proves the official central list pagination independently.
`reconcile_source_ids.py` separately discovers stable IDs used for recovery.  Both can report
"complete" while seeing different populations, so publication must require their central-source
stable-ID counts to agree within the same audit run.  The central pagination proof is intentionally
run again after reconciliation; a mismatch therefore also catches postings that appeared during
a long audit and prevents a mixed-time snapshot from being published as complete.
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
        central_count = int(src.get("stableIdCount") or 0)
        recon_count = int(other.get("officialIdCount") or 0)
        item = {"name": name, "centralStableIds": central_count, "reconciliationOfficialIds": recon_count}
        checked.append(item)
        if central_count != recon_count:
            mismatches.append(item)

    if len(checked) != 2:
        raise SystemExit(f"Expected exactly two central sources, checked={checked}, mismatches={mismatches}")
    if mismatches:
        raise SystemExit("Independent central-source ID populations disagree: " + json.dumps(mismatches, ensure_ascii=False))

    print(json.dumps({"state": "ok", "timestampSkewMinutes": round(skew, 2), "sources": checked}, ensure_ascii=False))


if __name__ == "__main__":
    main()
