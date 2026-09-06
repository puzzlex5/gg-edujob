#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import re

import crawl_artmore_browser as base


async def enable_current_only(page):
    """Use the same proven current-only submit path as the isolated v3 probe."""
    cur = page.locator("#jobs_ving_chk")
    hidden = page.locator("#exclude_end_yn")
    if not await cur.count() or not await hidden.count():
        raise RuntimeError("current-only controls missing")
    if await hidden.input_value() == "Y":
        return
    if await cur.is_checked():
        await cur.evaluate("e=>{e.checked=false}")
    try:
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
            await cur.click(force=True)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(500)
    if await page.locator("#exclude_end_yn").input_value() == "Y":
        return
    if not await page.evaluate("()=>typeof window.jQuery==='function'"):
        raise RuntimeError("jQuery unavailable for current-only fallback")
    await page.evaluate("""()=>{const $=window.jQuery,$c=$('#jobs_ving_chk');if(!$c.length)throw new Error('jobs_ving_chk missing');$c.prop('checked',false);$c.trigger('click');}""")
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception:
        pass
    await page.wait_for_timeout(700)
    if await page.locator("#exclude_end_yn").input_value() != "Y":
        raise RuntimeError("current-only handler did not set Y")


async def raw_page_ids(page):
    hrefs = await page.locator('a[href*="rec_idx="]').evaluate_all("els=>els.map(a=>a.getAttribute('href')||'')")
    ids, seen = [], set()
    for href in hrefs:
        m = base.REC_RE.search(href)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            ids.append(m.group(1))
    return ids


async def page_rows(page, region: str):
    anchors = page.locator('a[href*="rec_idx="]')
    rows, seen = [], set()
    for i in range(await anchors.count()):
        a = anchors.nth(i)
        href = await a.get_attribute("href") or ""
        m = base.REC_RE.search(href)
        if not m:
            continue
        rid = m.group(1)
        if rid in seen:
            continue
        seen.add(rid)
        title = re.sub(r"\s+", " ", (await a.inner_text()).strip())
        text = await a.evaluate("""a=>{let n=a,best=(a.innerText||'').replace(/\s+/g,' ').trim();for(let i=0;i<9&&n;i++,n=n.parentElement){const t=(n.innerText||'').replace(/\s+/g,' ').trim();const ids=[...n.querySelectorAll('a[href*="rec_idx="]')].map(x=>(x.getAttribute('href')||'').match(/rec_idx=(\d+)/)).filter(Boolean).map(x=>x[1]);const uniq=[...new Set(ids)];if(uniq.length===1&&uniq[0]===(a.getAttribute('href')||'').match(/rec_idx=(\d+)/)?.[1]&&t.length>best.length&&t.length<1400){best=t;continue}if(uniq.length>1)break}return best}""")
        other = "경기" if region == "서울" else "서울"
        if base.REGION_PATTERNS[other].search(text):
            raise RuntimeError(f"cross-metro contamination in {region}: {rid} {text[:180]}")
        # Sponsored/global rows can appear on a filtered page. They are pagination
        # evidence, but must never be mislabeled as the selected metro region.
        if not base.REGION_PATTERNS[region].search(text):
            continue
        if base.END_RE.search(text) and "진행중" not in text:
            raise RuntimeError(f"ended row on current-only surface: {rid} {text[:180]}")
        dates = base.DATE_RE.findall(text)
        registered = base.iso_date(dates[0]) if dates else ""
        apply_end = base.iso_date(dates[-1]) if len(dates) >= 2 else ""
        pat = r"서울(?:특별시)?\s+[^\s]+(?:구|군)(?:\s+[^\s]+){0,4}" if region == "서울" else r"경기(?:도)?\s+[^\s]+(?:시|군)(?:\s+[^\s]+){0,4}"
        lm = re.search(pat, text)
        location = lm.group(0).strip() if lm else ""
        rows.append({
            "sourceIdentity": f"artmore:{rid}",
            "source": "아트모아",
            "sourceSurface": "culture-arts",
            "sourceSurfaceLabel": "아트모아 문화예술 채용",
            "stableId": rid,
            "province": region,
            "location": location,
            "title": title,
            "registered": registered,
            "applyEnd": apply_end,
            "originalUrl": base.urljoin(base.URL, href),
            "url": base.urljoin(base.URL, href),
            "linkedOfficialUrls": [],
            "rawListText": text,
        })
    return rows


async def collect_surface(browser, region: str, code: str):
    page = await browser.new_page(locale="ko-KR")
    try:
        expected = await base.establish_filter(page, region, code)
        out, page_reports, seen_ids, seen_raw_signatures = [], [], set(), set()
        max_pages = int(__import__("os").environ.get("ARTMORE_MAX_PAGES", "250"))
        termination_reason = None
        for n in range(1, max_pages + 1):
            if n > 1 and not await base.goto_page(page, n):
                termination_reason = "pagination-repeated-page"
                break
            body = await page.locator("body").inner_text()
            if base.BLOCK_RE.search(body):
                raise RuntimeError(f"human-check/block on page {n}")
            area_vals = await page.locator('input[name="array_area_type"]').evaluate_all("els=>els.map(e=>e.value)")
            current = await page.locator("#exclude_end_yn").input_value() if await page.locator("#exclude_end_yn").count() else ""
            if expected not in area_vals or current != "Y":
                raise RuntimeError(f"filter drift page={n}: area={area_vals} current={current}")
            raw_ids = await raw_page_ids(page)
            sig = tuple(raw_ids)
            if not raw_ids:
                termination_reason = "raw-page-empty"
                page_reports.append({"page": n, "rawIdCount": 0, "rowCount": 0, "rawNewIdCount": 0, "newIdCount": 0, "firstId": None, "lastId": None, "terminationReason": termination_reason})
                break
            if sig in seen_raw_signatures:
                termination_reason = "raw-page-repeated"
                page_reports.append({"page": n, "rawIdCount": len(raw_ids), "rowCount": 0, "rawNewIdCount": 0, "newIdCount": 0, "firstId": raw_ids[0], "lastId": raw_ids[-1], "terminationReason": termination_reason})
                break
            seen_raw_signatures.add(sig)
            rows = await page_rows(page, region)
            ids = [r["stableId"] for r in rows]
            raw_new = [x for x in raw_ids if x not in seen_ids]
            new = [x for x in ids if x not in seen_ids]
            page_reports.append({"page": n, "rawIdCount": len(raw_ids), "rowCount": len(rows), "rawNewIdCount": len(raw_new), "newIdCount": len(new), "firstId": raw_ids[0], "lastId": raw_ids[-1], "terminationReason": None})
            for r in rows:
                if r["stableId"] not in seen_ids:
                    seen_ids.add(r["stableId"])
                    out.append(r)
        if termination_reason is None:
            raise RuntimeError(f"max page safety cap reached before proven exhaustion for {region}: {max_pages}")
        if page_reports:
            page_reports[-1]["terminationReason"] = termination_reason
        if not out:
            raise RuntimeError(f"zero rows for {region}")
        return out, page_reports
    finally:
        await page.close()


base.enable_current_only = enable_current_only
base.page_rows = page_rows
base.collect_surface = collect_surface

if __name__ == "__main__":
    raise SystemExit(asyncio.run(base.main()))
