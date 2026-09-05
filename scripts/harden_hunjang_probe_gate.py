#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

REPORT = Path("hunjang_probe_report.json")

# APIs that can contain recruitment rows but are not evidence of the public general
# recruitment listing. They must never unlock pagination/canonical publication gates.
NON_GENERAL_MARKERS = (
    "/hunApi/main/",
    "/hunApi/common/",
    "/logApi/",
    "/recommRecruit/",
    "/recommendRecruit/",
    "/recommend/",
)


def main() -> int:
    if not REPORT.exists():
        raise SystemExit("missing hunjang_probe_report.json")

    report = json.loads(REPORT.read_text(encoding="utf-8"))
    urls = [str(u or "") for u in report.get("observedGeneralListApiUrls", [])]
    accepted = [u for u in urls if u and not any(marker in u for marker in NON_GENERAL_MARKERS)]
    rejected = [u for u in urls if u not in accepted]

    report["observedGeneralListApiUrlsRaw"] = urls
    report["rejectedNonGeneralRecruitApiUrls"] = rejected
    report["observedGeneralListApiUrls"] = accepted
    report["observedGeneralListApiCount"] = len(accepted)

    has_stable_id = bool(report.get("stableIdCandidate") and report.get("numericRcrtNoCount", 0))
    blocked = bool(report.get("humanVerificationDetected"))
    report["readyForPaginationProbe"] = bool(has_stable_id and accepted and not blocked)

    if not accepted:
        report["nextGate"] = (
            "discover the public general recruitment-list API from the rendered 채용정보 view; "
            "recommendation/main/common APIs do not qualify. Then prove page-wise ID changes and "
            "an exhaustion boundary before writing any canonical dataset"
        )

    safety = report.setdefault("safety", {})
    safety.setdefault("tlsVerificationDisabled", False)
    safety.setdefault("authenticationBypassed", False)
    safety.setdefault("captchaBypassed", False)
    safety["publicationAttempted"] = False

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "rawCandidateUrls": urls,
        "acceptedGeneralListUrls": accepted,
        "rejectedNonGeneralUrls": rejected,
        "readyForPaginationProbe": report["readyForPaginationProbe"],
        "nextGate": report.get("nextGate"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
