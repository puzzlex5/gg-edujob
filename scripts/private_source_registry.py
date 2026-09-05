#!/usr/bin/env python3
"""Shared registry and publication gates for private recruitment sources."""
from __future__ import annotations

import re

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
        "detail_url_patterns": (
            r"^https?://(?:www\.)?jobteacher\.kr/employ/detail/\d+/?(?:[?#].*)?$",
        ),
    },
    {
        "key": "artmore",
        "name": "아트모아",
        "jobs": "artmore_jobs.json",
        "report": "artmore_reconciliation_report.json",
        "detail_report": "artmore_detail_link_report.json",
        "detail_url_patterns": (
            r"^https?://(?:www\.)?artmore\.kr/sub/recruit/search_view\.do\?[^#]*\brec_idx=\d+",
        ),
    },
    {
        "key": "gonggonggangsa",
        "name": "공공강사",
        "jobs": "gonggonggangsa_jobs.json",
        "report": "gonggonggangsa_reconciliation_report.json",
        "detail_report": "gonggonggangsa_detail_link_report.json",
        "detail_url_patterns": (
            r"^https?://(?:www\.)?00gangsa\.com/recruitments/\d+/?(?:[?#].*)?$",
        ),
    },
)


def private_source_specs():
    return PRIVATE_SOURCES


def private_source_keys() -> tuple[str, ...]:
    return tuple(spec["key"] for spec in PRIVATE_SOURCES)


def publication_enabled(report) -> bool:
    return not isinstance(report, dict) or "publicationEnabled" not in report or report.get("publicationEnabled") is True


def detail_url_is_specific(spec, url: str) -> bool:
    """Return True only when a configured source URL identifies one posting, not a list/home page."""
    patterns = tuple(spec.get("detail_url_patterns") or ())
    if not patterns:
        return bool(str(url or "").strip())
    raw = str(url or "").strip()
    return any(re.search(pattern, raw, re.I) for pattern in patterns)


def source_health(spec, report, detail_report) -> bool:
    ok = bool(
        report
        and report.get("healthy")
        and report.get("traversalComplete")
        and int(report.get("missingAfterCount") or 0) == 0
    )
    if isinstance(report, dict) and "detailErrorCount" in report:
        ok = ok and int(report.get("detailErrorCount") or 0) == 0
    if spec.get("detail_report"):
        ok = ok and bool(
            detail_report
            and detail_report.get("healthy")
            and detail_report.get("detailCoverageComplete")
            and int(detail_report.get("detailErrorCount") or 0) == 0
        )
    return ok
