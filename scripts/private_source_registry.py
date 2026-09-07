#!/usr/bin/env python3
"""Shared registry and publication gates for private recruitment sources."""
from __future__ import annotations

import json
import re
from pathlib import Path

PRIVATE_SOURCES = (
    {
        "key": "lessoninfo",
        "name": "레슨인포",
        "jobs": "lessoninfo_jobs.json",
        "report": "lessoninfo_reconciliation_report.json",
        "detail_report": None,
        "detail_url_patterns": (
            r"^https?://(?:www\.)?lessoninfo\.co\.kr/culture-jobs/detail\.php\?[^#]*\bid=\d+",
            r"^https?://(?:www\.)?lessoninfo\.co\.kr/board/board\.php\?[^#]*\bbo_table=[^&#]+[^#]*\bwr_no=\d+",
        ),
    },
    {
        "key": "jobteacher",
        "name": "잡티처",
        "jobs": "jobteacher_jobs.json",
        "report": "jobteacher_reconciliation_report.json",
        "detail_report": "jobteacher_detail_link_report.json",
        "detail_url_patterns": (r"^https?://(?:www\.)?jobteacher\.kr/employ/detail/\d+/?(?:[?#].*)?$",),
    },
    {
        "key": "artmore", "name": "아트모아", "jobs": "artmore_jobs.json", "report": "artmore_reconciliation_report.json",
        "detail_report": "artmore_detail_link_report.json",
        "detail_url_patterns": (r"^https?://(?:www\.)?artmore\.kr/sub/recruit/search_view\.do\?[^#]*\brec_idx=\d+",),
    },
    {
        "key": "gonggonggangsa", "name": "공공강사", "jobs": "gonggonggangsa_jobs.json", "report": "gonggonggangsa_reconciliation_report.json",
        "detail_report": "gonggonggangsa_detail_link_report.json",
        "detail_url_patterns": (r"^https?://(?:www\.)?00gangsa\.com/recruitments/\d+/?(?:[?#].*)?$",),
    },
    {
        "key": "seekle", "name": "시립광진청소년센터", "jobs": "seekle_jobs.json", "report": "seekle_reconciliation_report.json", "detail_report": None,
        "detail_url_patterns": (r"^https?://(?:www\.)?seekle\.or\.kr/sub07/sub01\.php\?[^#]*\bidx=\d+[^#]*\bptype=view(?:[&#].*)?$",),
    },
    {
        "key": "boramyc", "name": "시립보라매청소년센터", "jobs": "boramyc_jobs.json", "report": "boramyc_reconciliation_report.json", "detail_report": None,
        "detail_url_patterns": (r"^https?://(?:www\.)?boramyc\.or\.kr/sub06/sub01\.php\?[^#]*\bidx=\d+[^#]*\bptype=view(?:[&#].*)?$",),
    },
)


def private_source_specs(): return PRIVATE_SOURCES

def private_source_keys() -> tuple[str, ...]: return tuple(spec["key"] for spec in PRIVATE_SOURCES)

def publication_enabled(report) -> bool:
    return not isinstance(report, dict) or "publicationEnabled" not in report or report.get("publicationEnabled") is True

def detail_url_is_specific(spec, url: str) -> bool:
    patterns = tuple(spec.get("detail_url_patterns") or ())
    if not patterns: return bool(str(url or "").strip())
    raw = str(url or "").strip()
    return any(re.search(pattern, raw, re.I) for pattern in patterns)

def lessoninfo_culture_failclosed(row) -> bool:
    """True only for culture rows that are not individually cold-browser verified.

    Culture postings remain searchable when verification fails, but only rows carrying an explicit
    ``detailLinkVerified=True`` result from the independent cold verifier may expose a clickable
    destination. This preserves the fail-closed boundary without disabling the entire surface.
    """
    row = row or {}
    return (
        str(row.get("sourceSurface") or "") == "culture-arts"
        and row.get("detailLinkVerified") is not True
    )

def _lessoninfo_exact_link_coverage(spec) -> bool:
    """Require exact per-post evidence for every current Lessoninfo row.

    Afterschool/nulbom rows must expose their exact Lessoninfo route. Culture rows may remain
    non-clickable, but they must have an explicit failed cold-verification result. A verified
    culture row must retain its exact Lessoninfo detail identity in ``detailUrl`` and a non-empty
    ``verifiedUrl`` for the destination actually exposed to users (official original when proven,
    otherwise the cold-safe Lessoninfo detail URL).
    """
    try:
        data = json.loads(Path(spec["jobs"]).read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("jobs", [])
        if not rows:
            return False
        for row in rows:
            if str((row or {}).get("sourceSurface") or "") == "culture-arts":
                if lessoninfo_culture_failclosed(row):
                    if row.get("detailLinkVerified") is not False:
                        return False
                    if row.get("url") or row.get("originalUrl") or row.get("verifiedUrl"):
                        return False
                    if not str(row.get("detailLinkReason") or row.get("detailLinkVerificationReason") or "").strip():
                        return False
                    continue
                detail = row.get("detailUrl") or row.get("unverifiedDetailUrl") or ""
                verified = row.get("verifiedUrl") or ""
                if not detail_url_is_specific(spec, detail) or not str(verified).strip():
                    return False
                if str(row.get("url") or "") != str(verified) or str(row.get("originalUrl") or "") != str(verified):
                    return False
                continue

            url = row.get("detailUrl") or row.get("originalUrl") or row.get("openUrl") or row.get("url") or ""
            if not detail_url_is_specific(spec, url):
                return False
        return True
    except Exception:
        return False

def source_health(spec, report, detail_report) -> bool:
    ok = bool(report and report.get("healthy") and report.get("traversalComplete") and int(report.get("missingAfterCount") or 0) == 0)
    if isinstance(report, dict) and "detailErrorCount" in report:
        ok = ok and int(report.get("detailErrorCount") or 0) == 0
    if spec.get("detail_report"):
        ok = ok and bool(detail_report and detail_report.get("healthy") and detail_report.get("detailCoverageComplete") and int(detail_report.get("detailErrorCount") or 0) == 0)
    if ok and spec.get("key") == "lessoninfo":
        ok = _lessoninfo_exact_link_coverage(spec)
    return ok
