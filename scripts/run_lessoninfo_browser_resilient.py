#!/usr/bin/env python3
"""Hardened Lessoninfo browser runner.

The integrated runner already applies current-only classification, Seoul/Gyeonggi restriction,
and explicit official-link capture. This wrapper adds three fail-closed postconditions:

1. retry only a transient empty list render (never CAPTCHA/access-denied challenges),
2. normalize accidentally contaminated location text without inventing a region,
3. require every published Lessoninfo item to resolve to its exact individual URL identity; for
   culture-jobs, also bind the expected list-row title to the final detail body. A redirect to a
   list/home page is a normal inactive/rejected classification, not a transient detail error that
   should retain stale last-known-good content.

Culture-job titles can also carry an explicit deadline such as ``(~8.21.)``. That evidence is
converted to the existing labelled-deadline format before base classification so an already expired
posting cannot survive as ``fresh-no-deadline`` merely because the detail page omits a labelled
마감일 field.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import run_lessoninfo_browser_integrated as integrated

b = integrated.b
_BASE_CRAWL_LIST = b.crawl_list
_BASE_CLASSIFY_DETAIL = b.classify_detail
MAX_EMPTY_ATTEMPTS = 3

# Deliberately narrow: only a parenthesized/bracketed title-tail style with an explicit leading '~'.
# Do not treat arbitrary dates in a title/body as deadlines.
_TITLE_DEADLINE_RE = re.compile(
    r"(?:\(|\[|（)\s*[~～]\s*((?:20\d{2}[.\-/])?\d{1,2}[.\-/]\d{1,2})\s*\.?\s*(?:\)|\]|）)",
    re.I,
)


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _compact_title(value: str) -> str:
    """Normalize a posting title for conservative content binding."""
    value = _norm(value)
    value = re.sub(r"^\(?\s*복사\s*\)?\s*", "", value, flags=re.I)
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", value).lower()


def _title_deadline(title: str, registered: str = "") -> str:
    """Return YYYY-MM-DD only for explicit ``(~date)`` / ``[~date]`` title evidence."""
    m = _TITLE_DEADLINE_RE.search(title or "")
    if not m:
        return ""
    ref = b.now_kst()
    if registered:
        try:
            ref = datetime.strptime(registered, "%Y-%m-%d").replace(tzinfo=ref.tzinfo)
        except ValueError:
            pass
    return b.parse_date(m.group(1), ref)


def _detail_route_matches(item: dict, final_url: str) -> bool:
    """Bind a navigation result to the exact Lessoninfo identity requested by the list row.

    ``ckattempt`` and other harmless session query additions are ignored. Identity-bearing ``id``
    or ``wr_no``/``bo_table`` values must match exactly. List/home/login redirects fail closed.
    """
    try:
        requested = urlparse(str(item.get("url") or ""))
        final = urlparse(str(final_url or ""))
        if (final.hostname or "").lower() not in {"lessoninfo.co.kr", "www.lessoninfo.co.kr"}:
            return False
        rq = parse_qs(requested.query, keep_blank_values=True)
        fq = parse_qs(final.query, keep_blank_values=True)
        surface = item.get("sourceSurface")
        if surface == "culture-arts":
            expected_id = (rq.get("id") or [""])[0]
            actual_id = (fq.get("id") or [""])[0]
            return (
                final.path.lower() == "/culture-jobs/detail.php"
                and bool(expected_id)
                and actual_id == expected_id
            )
        if surface == "afterschool-nulbom":
            expected_wr = (rq.get("wr_no") or [""])[0]
            expected_table = (rq.get("bo_table") or [""])[0]
            actual_wr = (fq.get("wr_no") or [""])[0]
            actual_table = (fq.get("bo_table") or [""])[0]
            return (
                final.path.lower() == "/board/board.php"
                and bool(expected_wr)
                and actual_wr == expected_wr
                and bool(expected_table)
                and actual_table == expected_table
            )
        return False
    except Exception:
        return False


def _detail_content_matches(item: dict, text: str) -> bool:
    """Require the culture list-row title to be represented in the final detail body.

    ``(복사)`` is ignored because Lessoninfo duplicates sometimes add that marker only on the list
    row. The afterschool board already carries strong ``wr_no`` + ``bo_table`` identity and is not
    subjected to this extra title-text rule, avoiding false negatives on legacy board title markup.
    """
    expected = _compact_title(str(item.get("titleHint") or ""))
    if len(expected) < 6:
        return False
    actual = _compact_title(text[:12000])
    return expected in actual


def _compact_verified_location(job: dict) -> None:
    """Collapse noisy container-derived location text to already-verified metro evidence."""
    location = _norm(str(job.get("location") or ""))
    region = _norm(str(job.get("metroRegion") or job.get("region") or job.get("province") or ""))
    if not location or region not in {"서울", "경기"}:
        return

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
    title_deadline = _title_deadline(
        str(item.get("titleHint") or ""), str(item.get("registeredHint") or "")
    )
    classified_text = text
    if title_deadline:
        classified_text = f"마감일자: {title_deadline}\n{text}"

    job, reason = _BASE_CLASSIFY_DETAIL(html, classified_text, item)
    if job:
        _compact_verified_location(job)
    return job, reason


async def fetch_detail_with_identity_guard(
    context, sem: asyncio.Semaphore, item: dict
) -> tuple[str, dict | None, str, str]:
    """Load one detail page and reject redirects/wrong content without stale-LKG retention."""
    async with sem:
        page = await context.new_page()
        try:
            html, text = await b.load(page, item["url"])
            if not _detail_route_matches(item, page.url):
                return item["sourceIdentity"], None, "detail-route-mismatch", ""
            if item.get("sourceSurface") == "culture-arts" and not _detail_content_matches(item, text):
                return item["sourceIdentity"], None, "detail-content-mismatch", ""
            job, reason = b.classify_detail(html, text, item)
            return item["sourceIdentity"], job, reason, ""
        except Exception as exc:
            return (
                item["sourceIdentity"],
                None,
                "detail-error",
                f"{type(exc).__name__}:{exc}",
            )
        finally:
            await page.close()


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
b.fetch_detail = fetch_detail_with_identity_guard
b.crawl_list = crawl_list_with_empty_retry


if __name__ == "__main__":
    raise SystemExit(b.main())
