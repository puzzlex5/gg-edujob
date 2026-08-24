#!/usr/bin/env python3
"""Fail closed when central-source completeness proofs are logically inconsistent.

There are deliberately three proofs:
1) `central_pagination_report.json` independently proves the current central list endpoints parse
   and paginate correctly (active-oriented Gyeonggi UI + Seoul central list).
2) `source_reconciliation_report.json` defines the authoritative recent 90-day population used for
   recovery.
3) `verify_gyeonggi_central_90d.py` independently re-traverses the Gyeonggi central portal with
   closed postings included and proves the same 90-day boundary without calling the reconciliation
   crawler.

The active Gyeonggi count is allowed to be a subset of the 90-day count, but the independent 90-day
count must exactly match reconciliation in the same audit window. This prevents a healthy active
view from masking a broken or inflated 90-day population.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CENTRAL = ROOT / "central_pagination_report.json"
RECON = ROOT / "source_reconciliation_report.json"
GG90 = ROOT / "gyeonggi_central_90d_report.json"
KST = timezone(timedelta(hours=9))
MAX_SKEW_MINUTES = 35


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_kst(value):
    return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S KST").replace(tzinfo=KST)


def source_map(report):
    return {str(x.get("name") or ""): x for x in (report.get("sources") or [])}


def main():
    central = load(CENTRAL)
    recon = load(RECON)
    if not central.get("complete"):
        raise SystemExit("Central pagination report is not complete")
    if int((recon.get("summary") or {}).get("reconciledSources") or 0) != 38:
        raise SystemExit("38-source reconciliation is not complete")
    if int((recon.get("summary") or {}).get("missingAfter") or 0) != 0:
        raise SystemExit("38-source reconciliation still has missing IDs")

    # Produce a fresh independent 90-day Gyeonggi proof in this same verification step.
    subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_gyeonggi_central_90d.py")], check=True)
    gg90 = load(GG90)
    if not gg90.get("complete"):
        raise SystemExit("Independent Gyeonggi 90-day report is not complete")

    try:
        ctime = parse_kst(central.get("generatedAt"))
        rtime = parse_kst(recon.get("generatedAt"))
        gtime = parse_kst(gg90.get("generatedAt"))
    except Exception as exc:
        raise SystemExit(f"Cannot prove audit timestamp consistency: {type(exc).__name__}")

    skew_cr = abs((ctime - rtime).total_seconds()) / 60
    skew_gr = abs((gtime - rtime).total_seconds()) / 60
    if skew_cr > MAX_SKEW_MINUTES:
        raise SystemExit(f"Central/reconciliation reports are from different audit windows: {skew_cr:.1f} minutes")
    if skew_gr > MAX_SKEW_MINUTES:
        raise SystemExit(f"Gyeonggi-90d/reconciliation reports are from different audit windows: {skew_gr:.1f} minutes")

    recon_sources = source_map(recon)
    central_sources = source_map(central)
    gg_name = "경기도교육청 통합 구인구직"
    se_name = "서울교육일자리포털"
    gg_recon = recon_sources.get(gg_name)
    se_recon = recon_sources.get(se_name)
    gg_active = central_sources.get(gg_name)
    se_central = central_sources.get(se_name)
    if not all((gg_recon, se_recon, gg_active, se_central)):
        raise SystemExit("Missing one or more central source records from verification reports")

    gg_recent_count = int(gg_recon.get("officialIdCount") or 0)
    gg_independent_count = int(gg90.get("stableIdCount") or 0)
    gg_active_count = int(gg_active.get("stableIdCount") or 0)
    se_recon_count = int(se_recon.get("officialIdCount") or 0)
    se_independent_count = int(se_central.get("stableIdCount") or 0)

    errors = []
    if gg_independent_count != gg_recent_count:
        errors.append({
            "source": gg_name,
            "reason": "independent-90d-count-mismatch",
            "reconciliation90d": gg_recent_count,
            "independent90d": gg_independent_count,
        })
    if gg_active_count > gg_recent_count:
        errors.append({
            "source": gg_name,
            "reason": "active-count-exceeds-90d",
            "active": gg_active_count,
            "reconciliation90d": gg_recent_count,
        })
    # Seoul's independent central verifier already uses the same central population semantics as
    # reconciliation, so require exact equality rather than a subset relation.
    if se_independent_count != se_recon_count:
        errors.append({
            "source": se_name,
            "reason": "central-count-mismatch",
            "reconciliation": se_recon_count,
            "independent": se_independent_count,
        })

    if errors:
        raise SystemExit("Central population consistency failed: " + json.dumps(errors, ensure_ascii=False))

    print(json.dumps({
        "state": "ok",
        "timestampSkewMinutes": {
            "centralVsReconciliation": round(skew_cr, 2),
            "gyeonggi90dVsReconciliation": round(skew_gr, 2),
        },
        "sources": [
            {
                "name": gg_name,
                "activeStableIds": gg_active_count,
                "reconciliation90dStableIds": gg_recent_count,
                "independent90dStableIds": gg_independent_count,
            },
            {
                "name": se_name,
                "reconciliationStableIds": se_recon_count,
                "independentStableIds": se_independent_count,
            },
        ],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
