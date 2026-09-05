#!/usr/bin/env python3
"""Retry only transient empty Lessoninfo list renders before failing closed.

The integrated runner already applies current-only classification, Seoul/Gyeonggi restriction,
and explicit official-link capture. This wrapper adds retries only for a transient empty list render;
it never retries or bypasses CAPTCHA/access-denied challenges and never weakens completeness guards.

It also normalizes any location value that accidentally captured surrounding list/container text.
Metro classification is already decided from posting-specific evidence by the fast runner; this
postcondition keeps the published location compact without inventing a different region.
"""
from __future__ import annotations

import asyncio
import re

import run_lessoninfo_browser_integrated as integrated

b = integrated.b
_BASE_CRAWL_LIST = b.crawl_list
_BASE_CLASSIFY_DETAIL = b.classify_detail
MAX_EMPTY_ATTEMPTS = 3


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _compact_verified_location(job: dict) -> None:
    """Collapse noisy container-derived location text to already-verified metro evidence."""
    location = _norm(str(job.get("location") or ""))
    region = _norm(str(job.get("metroRegion") or job.get("region") or job.get("province") or ""))
    if not location or region not in {"서울", "경기"}:
        return

    # Normal addresses/location labels are already useful. The contamination regression produced
    # long concatenated fragments containing institution names, titles, dates, and adjacent rows.
    noisy = (
        len(location) > 45
        or bool(re.search(r"\b\d{1,2}[./-]\d{1,2}\b", location))
        or len(re.findall(r"(?:채용|모집|구인|강사|교사|경력직|정규직|계약직)", location)) >= 2
    )
    if not noisy:
        return

    if region == "서울":
        m = re.search(r"서울(?:특별시|시)?\s*([가-힣]{1,8}구)?", location)
        if m:
            compact = _norm(m.group(0))
            if compact:
                job["location"] = compact
                return
        for district in integrated.run_lessoninfo_browser_fast.SEOUL_DISTRICTS:
            if district in location:
                job["location"] = f"서울 {district}"
                return
        job["location"] = "서울"
        return

    m = re.search(r"경기(?:도)?\s*([가-힣]{1,10}(?:시|군))?", location)
    if m:
        compact = _norm(m.group(0))
        if compact:
            job["location"] = compact
            return
    for place in integrated.run_lessoninfo_browser_fast.GYEONGGI_PLACES:
        if place in location:
            job["location"] = f"경기 {place}"
            return
    job["location"] = "경기"


def classify_with_location_postcondition(html: str, text: str, item: dict):
    job, reason = _BASE_CLASSIFY_DETAIL(html, text, item)
    if job:
        _compact_verified_location(job)
    return job, reason


async def crawl_list_with_empty_retry(context, target: dict):
    last_rows = []
    last_report = {}
    for attempt in range(1, MAX_EMPTY_ATTEMPTS + 1):
        rows, report = await _BASE_CRAWL_LIST(context, target)
        last_rows, last_report = rows, report
        transient_empty = (
            not rows
            and report.get("ids", 0) == 0
            and report.get("stopReason") == "unexpected_empty_first_page"
        )
        if not transient_empty:
            if attempt > 1:
                report["emptyRenderAttempts"] = attempt - 1
                report["recoveredAfterEmptyRender"] = True
            return rows, report
        if attempt < MAX_EMPTY_ATTEMPTS:
            await asyncio.sleep(2 * attempt)

    last_report["emptyRenderAttempts"] = MAX_EMPTY_ATTEMPTS
    last_report["recoveredAfterEmptyRender"] = False
    return last_rows, last_report


b.classify_detail = classify_with_location_postcondition
b.crawl_list = crawl_list_with_empty_retry


if __name__ == "__main__":
    raise SystemExit(b.main())
