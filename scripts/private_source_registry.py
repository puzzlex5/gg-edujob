#!/usr/bin/env python3
"""Shared registry and publication gates for private recruitment sources."""
from __future__ import annotations

PRIVATE_SOURCES = (
    {
        "key": "lessoninfo",
        "name": "레슨인포",
        "jobs": "lessoninfo_jobs.json",
        "report": "lessoninfo_reconciliation_report.json",
        "detail_report": None,
    },
    {
        "key": "jobteacher",
        "name": "잡티처",
        "jobs": "jobteacher_jobs.json",
        "report": "jobteacher_reconciliation_report.json",
        "detail_report": "jobteacher_detail_link_report.json",
    },
    {
        "key": "artmore",
        "name": "아트모아",
        "jobs": "artmore_jobs.json",
        "report": "artmore_reconciliation_report.json",
        "detail_report": "artmore_detail_link_report.json",
    },
    {
        "key": "gonggonggangsa",
        "name": "공공강사",
        "jobs": "gonggonggangsa_jobs.json",
        "report": "gonggonggangsa_reconciliation_report.json",
        "detail_report": "gonggonggangsa_detail_link_report.json",
    },
)


def private_source_specs():
    return PRIVATE_SOURCES


def private_source_keys() -> tuple[str, ...]:
    return tuple(spec["key"] for spec in PRIVATE_SOURCES)


def publication_enabled(report) -> bool:
    return not isinstance(report, dict) or "publicationEnabled" not in report or report.get("publicationEnabled") is True


def source_health(spec, report, detail_report) -> bool:
    ok = bool(
        report
        and report.get("healthy")
        and report.get("traversalComplete")
        and int(report.get("missingAfterCount") or 0) == 0
    )
    # Some sources (currently Lessoninfo) keep detail-link evidence in their main reconciliation
    # report rather than a separate detail report. Treat the presence of the field as the contract
    # instead of hard-coding a source key so future sources cannot silently skip this guard.
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
