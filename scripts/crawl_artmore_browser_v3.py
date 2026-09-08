#!/usr/bin/env python3
from __future__ import annotations

import asyncio

import crawl_artmore_browser as base
import crawl_artmore_browser_v2  # noqa: F401 - installs stricter page_rows on base


async def establish_filter(page, region: str, code: str):
    """Apply current-only before region selection so the first submit cannot erase area state."""
    await page.goto(base.URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(700)
    body = await page.locator("body").inner_text()
    if base.BLOCK_RE.search(body):
        raise RuntimeError("human-check/block page detected")

    # The site submits the form when current-only is toggled. Doing this first avoids
    # losing the region selector that the older sequence staged before that submit.
    await base.enable_current_only(page)

    if not await base.visible_click(page.get_by_role("button", name="지역 선택")):
        raise RuntimeError("region opener missing")
    await page.wait_for_timeout(250)

    radio = page.locator(f"#area_level_{code}")
    if not await radio.count():
        raise RuntimeError(f"region radio missing: {region}/{code}")
    await radio.evaluate('e=>{e.click();e.dispatchEvent(new Event("change",{bubbles:true}))}')
    await page.wait_for_timeout(500)

    all_radio = page.locator("#all_3")
    if not await all_radio.count():
        raise RuntimeError("region all selector missing")
    await all_radio.evaluate('e=>{e.click();e.dispatchEvent(new Event("change",{bubbles:true}))}')
    await page.wait_for_timeout(250)

    expected = f"2000-{code}"
    vals = await page.locator('input[name="area_selector_val"]').evaluate_all("els=>els.map(e=>e.value)")
    if expected not in vals:
        raise RuntimeError(f"area selector not staged: {expected}; got={vals}")

    if not await base.visible_click(page.locator("#btn_area_ok")):
        raise RuntimeError("region confirm button missing")
    await page.wait_for_timeout(350)

    vals = await page.locator('input[name="array_area_type"]').evaluate_all("els=>els.map(e=>e.value)")
    if expected not in vals:
        raise RuntimeError(f"area selector not committed: {expected}; got={vals}")

    # Submit the now-complete filter state once, then verify both invariants survived.
    if not await base.visible_click(page.get_by_role("button", name="검색하기")):
        raise RuntimeError("search submit button missing")
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception:
        pass
    await page.wait_for_timeout(600)

    vals = await page.locator('input[name="array_area_type"]').evaluate_all("els=>els.map(e=>e.value)")
    current = await page.locator("#exclude_end_yn").input_value() if await page.locator("#exclude_end_yn").count() else ""
    if expected not in vals:
        raise RuntimeError(f"area filter lost after final submit: expected={expected}; got={vals}")
    if current != "Y":
        raise RuntimeError(f"current-only filter lost after final submit: got={current!r}")
    return expected


base.establish_filter = establish_filter


if __name__ == "__main__":
    raise SystemExit(asyncio.run(base.main()))
