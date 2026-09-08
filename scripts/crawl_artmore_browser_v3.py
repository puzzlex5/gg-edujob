#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import re
from datetime import date

import crawl_artmore_browser as base


def _expired(apply_end: str) -> bool:
    if not apply_end:
        return False
    try:
        return date.fromisoformat(apply_end) < base.TODAY
    except Exception:
        return False


async def page_scan(page, region: str):
    anchors = page.locator('a[href*="rec_idx="]')
    active = []
    raw_ids = []
    excluded = []
    seen = set()
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
        raw_ids.append(rid)
        title = re.sub(r"\s+", " ", (await a.inner_text()).strip())
        text = await a.evaluate("""a=>{
          let n=a,b=(a.innerText||'').replace(/\s+/g,' ').trim();
          for(let i=0;i<9&&n;i++,n=n.parentElement){
            const t=(n.innerText||'').replace(/\s+/g,' ').trim();
            const ids=[...n.querySelectorAll('a[href*="rec_idx="]')]
              .map(x=>(x.getAttribute('href')||'').match(/rec_idx=(\d+)/))
              .filter(Boolean).map(x=>x[1]);
            const u=[...new Set(ids)];
            if(u.length===1 && u[0]===(a.getAttribute('href')||'').match(/rec_idx=(\d+)/)?.[1]
               && t.length>b.length && t.length<1400){b=t;continue}
            if(u.length>1) break;
          }
          return b;
        }""")
        dates = base.DATE_RE.findall(text)
        registered = base.iso_date(dates[0]) if dates else ""
        apply_end = base.iso_date(dates[-1]) if len(dates) >= 2 else ""
        other = "경기" if region == "서울" else "서울"
        if not base.REGION_PATTERNS[region].search(text):
            continue
        if base.REGION_PATTERNS[other].search(text):
            raise RuntimeError(f"cross-metro contamination in {region}: {rid} {text[:180]}")
        explicit_active = "진행중" in text
        ended = (bool(base.END_RE.search(text)) and not explicit_active) or (_expired(apply_end) and not explicit_active)
        if ended:
            excluded.append({"stableId": rid, "applyEnd": apply_end, "reason": "ended-or-expired", "text": text[:240]})
            continue
        location = ""
        if region == "서울":
            lm = re.search(r"서울(?:특별시)?\s+[^\s]+(?:구|군)(?:\s+[^\s]+){0,4}", text)
        else:
            lm = re.search(r"경기(?:도)?\s+[^\s]+(?:시|군)(?:\s+[^\s]+){0,4}", text)
        if lm:
            location = lm.group(0).strip()
        active.append({
            "sourceIdentity": f"artmore:{rid}", "source": "아트모아",
            "sourceSurface": "culture-arts", "sourceSurfaceLabel": "아트모아 문화예술 채용",
            "stableId": rid, "province": region, "location": location, "title": title,
            "registered": registered, "applyEnd": apply_end,
            "originalUrl": base.urljoin(base.URL, href), "url": base.urljoin(base.URL, href),
            "linkedOfficialUrls": [], "rawListText": text,
        })
    return active, raw_ids, excluded


async def collect_surface(browser, region: str, code: str):
    page = await browser.new_page(locale="ko-KR")
    try:
        expected = await base.establish_filter(page, region, code)
        out, reports, seen_active, seen_raw = [], [], set(), set()
        max_pages = int(__import__("os").environ.get("ARTMORE_MAX_PAGES", "250"))
        for n in range(1, max_pages + 1):
            if n > 1 and not await base.goto_page(page, n):
                break
            body = await page.locator("body").inner_text()
            if base.BLOCK_RE.search(body):
                raise RuntimeError(f"human-check/block on page {n}")
            area_vals = await page.locator('input[name="array_area_type"]').evaluate_all("els=>els.map(e=>e.value)")
            current = await page.locator("#exclude_end_yn").input_value() if await page.locator("#exclude_end_yn").count() else ""
            if expected not in area_vals or current != "Y":
                raise RuntimeError(f"filter drift page={n}: area={area_vals} current={current}")
            rows, raw_ids, excluded = await page_scan(page, region)
            new_raw = [x for x in raw_ids if x not in seen_raw]
            new_active = [r for r in rows if r["stableId"] not in seen_active]
            reports.append({"page": n, "rawIdCount": len(raw_ids), "newRawIdCount": len(new_raw),
                            "activeCount": len(rows), "newActiveCount": len(new_active),
                            "excludedEndedCount": len(excluded), "excludedEnded": excluded[:20],
                            "firstRawId": raw_ids[0] if raw_ids else None, "lastRawId": raw_ids[-1] if raw_ids else None})
            if not raw_ids or not new_raw:
                break
            seen_raw.update(new_raw)
            for r in new_active:
                seen_active.add(r["stableId"])
                out.append(r)
            if len(raw_ids) < 10:
                break
        if len(reports) >= max_pages and reports[-1].get("newRawIdCount"):
            raise RuntimeError(f"max page safety cap reached for {region}: {max_pages}")
        if not out:
            raise RuntimeError(f"zero active rows for {region}")
        return out, reports
    finally:
        await page.close()


base.collect_surface = collect_surface

if __name__ == "__main__":
    raise SystemExit(asyncio.run(base.main()))
