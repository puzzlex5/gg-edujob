#!/usr/bin/env python3
"""Retry only transient empty Lessoninfo list renders before failing closed.

The underlying Chromium collector already preserves the last known-good dataset when traversal
is incomplete. This wrapper does not retry CAPTCHA/access-denied errors and does not weaken any
classification or completeness guard. It only retries the specific case where a list page loads
without any discoverable posting IDs, which has been observed intermittently after a healthy run.
"""
from __future__ import annotations

import asyncio

import run_lessoninfo_browser_fast as fast

b = fast.b
_BASE_CRAWL_LIST = b.crawl_list
MAX_EMPTY_ATTEMPTS = 3


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


b.crawl_list = crawl_list_with_empty_retry


if __name__ == "__main__":
    raise SystemExit(b.main())
