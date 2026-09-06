#!/usr/bin/env python3
"""Fail closed when central-source completeness proofs are logically inconsistent.

There are deliberately three proofs:
1) `central_pagination_report.json` independently proves the current central list endpoints parse
   and paginate correctly (active-oriented Gyeonggi UI + Seoul central list).
2) `source_reconciliation_report.json` defines the authoritative recent 90-day population used for
   recovery.
3) `gyeonggi_central_90d_report.json` is produced by an explicit independent traversal before this
   verifier runs. This verifier is read-only: it must not regenerate that proof, because repeated
   regeneration later in a long reconciliation run moves the proof timestamp forward and can make
   an otherwise valid single audit window fail its own bounded-skew policy.

The public boards are live during an audit that can take tens of minutes, so exact count equality
between two complete scans is not a valid invariant: newly posted or boundary-expiring rows can
legitimately change the population while the audit is running. We therefore allow only small,
bounded temporal drift between independently complete scans. Large drift, stale audit windows,
incomplete traversal, or an active Gyeonggi population larger than the 90-day population still
fail closed.
"""
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CENTRAL = ROOT / "central_pagination_report.json"
RECON = ROOT / "source_reconciliation_report.json"
GG90 = ROOT / "gyeonggi_central_90d_report.json"
KST = timezone(timedelta(hours=9))
# Keep the window bounded to one audit chain. The independent 90-day proof is generated once by
# the workflow and then reused by this read-only consistency verifier; repeated verifier calls do
# not move its timestamp forward anymore.
MAX_SKEW_MINUTES = 45
MAX_DRIFT_RATIO = 0.01
MAX_DRIFT_ABSOLUTE = 25
MIN_DRIFT_ABSOLUTE = 3


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_kst(value):
    return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S KST").replace(tzinfo=KST)


def source_map(report):
    return {str(x.get("name") or ""): x for x in (report.get("sources") or [])}


def drift_limit(a, b):
    base = max(int(a or 0), int(b or 0), 1)
    return min(MAX_DRIFT_ABSOLUTE, max(MIN_DRIFT_ABSOLUTE, math.ceil(base * MAX_DRIFT_RATIO)))


def within_live_drift(a, b):
    return abs(int(a) - int(b)) <= drift_limit(a, b)


def main():
    central = load(CENTRAL)
    recon = load(RECON)
    if not central.get("complete"):
        raise SystemExit("Central pagination report is not complete")
    if int((recon.get("summary") or {}).get("reconciledSources") or 0) != 38:
        raise SystemExit("38-source reconciliation is not complete")
    if int((recon.get("summary") or {}).get("missingAfter") or 0) != 0:
        raise SystemExit("38-source reconciliation still has missing IDs")

    if not GG90.exists() or GG90.stat().st_size < 100:
        raise SystemExit("Independent Gyeonggi 90-day proof artifact missing")
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
    if not within_live_drift(gg_independent_count, gg_recent_count):
        errors.append({
            "source": gg_name,
            "reason": "independent-90d-drift-exceeds-live-window",
            "reconciliation90d": gg_recent_count,
            "independent90d": gg_independent_count,
            "difference": abs(gg_independent_count - gg_recent_count),
            "allowedDifference": drift_limit(gg_independent_count, gg_recent_count),
        })
    if gg_active_count > max(gg_recent_count, gg_independent_count):
        errors.append({
            "source": gg_name,
            "reason": "active-count-exceeds-90d",
            "active": gg_active_count,
            "reconciliation90d": gg_recent_count,
            "independent90d": gg_independent_count,
        })
    if not within_live_drift(se_independent_count, se_recon_count):
        errors.append({
            "source": se_name,
            "reason": "central-drift-exceeds-live-window",
            "reconciliation": se_recon_count,
            "independent": se_independent_count,
            "difference": abs(se_independent_count - se_recon_count),
            "allowedDifference": drift_limit(se_independent_count, se_recon_count),
        })

    if errors:
        raise SystemExit("Central population consistency failed: " + json.dumps(errors, ensure_ascii=False))

    print(json.dumps({
        "state": "ok",
        "policy": "complete independent scans may differ only by bounded live-source drift",
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
                "difference": abs(gg_independent_count - gg_recent_count),
                "allowedDifference": drift_limit(gg_independent_count, gg_recent_count),
            },
            {
                "name": se_name,
                "reconciliationStableIds": se_recon_count,
                "independentStableIds": se_independent_count,
                "difference": abs(se_independent_count - se_recon_count),
                "allowedDifference": drift_limit(se_independent_count, se_recon_count),
            },
        ],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
