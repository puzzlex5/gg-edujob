#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from cultural_foundation_common import load_foundations, generated_at


def valid_http(url: str) -> bool:
    try:
        p = urlparse(str(url or ""))
        return p.scheme in {"http", "https"} and bool(p.hostname)
    except Exception:
        return False


def main() -> int:
    foundations = load_foundations()
    errors = []
    ids = [str(x.get("id") or "") for x in foundations]
    if len(foundations) != 49:
        errors.append(f"operational foundation count={len(foundations)} expected=49")
    if len(set(ids)) != len(ids):
        errors.append("duplicate operational foundation ids")
    bad_homepages = [x.get("id") for x in foundations if not valid_http(x.get("homepage"))]
    if bad_homepages:
        errors.append(f"missing/invalid homepages: {bad_homepages}")
    bad_recruit = [x.get("id") for x in foundations if x.get("officialRecruitmentUrl") and not valid_http(x.get("officialRecruitmentUrl"))]
    if bad_recruit:
        errors.append(f"invalid recruitment routes: {bad_recruit}")
    by_region = {
        "서울": sum(1 for x in foundations if x.get("region") == "서울"),
        "경기": sum(1 for x in foundations if x.get("region") == "경기"),
    }
    if by_region != {"서울": 24, "경기": 25}:
        errors.append(f"region counts mismatch: {by_region}")
    report = {
        "generatedAt": generated_at(),
        "healthy": not errors,
        "institutions": len(foundations),
        "byRegion": by_region,
        "homepageCoverage": sum(1 for x in foundations if valid_http(x.get("homepage"))),
        "explicitRecruitmentRouteCoverage": sum(1 for x in foundations if valid_http(x.get("officialRecruitmentUrl"))),
        "discoveryRequired": [x.get("id") for x in foundations if not x.get("officialRecruitmentUrl")],
        "errors": errors,
    }
    Path("cultural_foundation_routes_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
