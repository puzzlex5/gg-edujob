#!/usr/bin/env python3
"""Fast runner for the browser-rendered Lessoninfo collector.

Lessoninfo list/detail pages are server-rendered. Waiting for global network-idle is expensive
because ad/analytics requests can remain active, so readiness is based on DOMContentLoaded plus
a short render window. Human-verification detection remains unchanged and is never bypassed.
"""
from __future__ import annotations

import crawl_lessoninfo_browser as b


async def fast_load(page, url: str):
    await page.goto(url, wait_until="domcontentloaded", timeout=35000)
    await page.wait_for_timeout(450)
    text = await b.visible_text(page)
    html = await page.content()
    if b.CHALLENGE_RE.search(text[:10000]):
        raise RuntimeError("human_verification_challenge")
    return html, text


b.load = fast_load

if __name__ == "__main__":
    raise SystemExit(b.main())
