#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

REPORT = Path("hunjang_probe_report.json")
SUMMARY = Path("hunjang_probe_summary.json")


def main() -> int:
    if not REPORT.exists():
        raise SystemExit("missing hunjang_probe_report.json")

    report = json.loads(REPORT.read_text(encoding="utf-8"))
    attempts = report.get("navigationAttempts") or []
    api_urls = []
    for attempt in attempts:
        for req in attempt.get("newApiRequests") or []:
            url = str(req.get("url") or "")
            if "/hunApi/" in url and url not in api_urls:
                api_urls.append(url)

    summary = {
        "generatedAt": report.get("generatedAt"),
        "source": report.get("source"),
        "publiclyReachable": bool(report.get("publiclyReachable")),
        "humanVerificationDetected": bool(report.get("humanVerificationDetected")),
        "stableIdCandidate": report.get("stableIdCandidate"),
        "numericRcrtNoCount": int(report.get("numericRcrtNoCount") or 0),
        "encRcrtNoCount": int(report.get("encRcrtNoCount") or 0),
        "metroRowCount": int(report.get("metroRowCount") or 0),
        "observedRecruitApiCount": int(report.get("observedRecruitApiCount") or 0),
        "observedPaidMainApiCount": int(report.get("observedPaidMainApiCount") or 0),
        "observedGeneralListApiCount": int(report.get("observedGeneralListApiCount") or 0),
        "observedGeneralListApiUrls": report.get("observedGeneralListApiUrls") or [],
        "observedGeneralListApiUrlsRaw": report.get("observedGeneralListApiUrlsRaw") or [],
        "rejectedNonGeneralRecruitApiUrls": report.get("rejectedNonGeneralRecruitApiUrls") or [],
        "observedDetailApiCount": int(report.get("observedDetailApiCount") or 0),
        "navigationHunApiUrls": api_urls[:200],
        "navigationAttemptCount": len(attempts),
        "readyForPaginationProbe": bool(report.get("readyForPaginationProbe")),
        "nextGate": report.get("nextGate"),
        "errors": (report.get("errors") or [])[:30],
        "safety": report.get("safety") or {},
    }

    # The compact summary is observational only. It cannot promote a source or alter
    # canonical/unified datasets; publication remains gated elsewhere.
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
