#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import re

import crawl_artmore_browser as base


async def page_rows(page, region: str):
    anchors = page.locator('a[href*="rec_idx="]')
    rows = []
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
        title = re.sub(r"\s+", " ", (await a.inner_text()).strip())
        text = await a.evaluate(
            """a=>{
              let n=a, best=(a.innerText||'').replace(/\s+/g,' ').trim();
              for(let i=0;i<9&&n;i++,n=n.parentElement){
                const t=(n.innerText||'').replace(/\s+/g,' ').trim();
                const ids=[...n.querySelectorAll('a[href*="rec_idx="]')]
                  .map(x=>(x.getAttribute('href')||'').match(/rec_idx=(\d+)/))
                  .filter(Boolean).map(x=>x[1]);
                const uniq=[...new Set(ids)];
                if(uniq.length===1 && uniq[0]===(a.getAttribute('href')||'').match(/rec_idx=(\d+)/)?.[1]
                   && t.length>best.length && t.length<1400){
                  best=t;
                  continue;
                }
                if(uniq.length>1) break;
              }
              return best;
            }"""
        )
        dates = base.DATE_RE.findall(text)
        registered = base.iso_date(dates[0]) if dates else ""
        apply_end = base.iso_date(dates[-1]) if len(dates) >= 2 else ""
        location = ""
        if region == "서울":
            lm = re.search(r"서울(?:특별시)?\s+[^\s]+(?:구|군)(?:\s+[^\s]+){0,4}", text)
        else:
            lm = re.search(r"경기(?:도)?\s+[^\s]+(?:시|군)(?:\s+[^\s]+){0,4}", text)
        if lm:
            location = lm.group(0).strip()
        other = "경기" if region == "서울" else "서울"
        if not base.REGION_PATTERNS[region].search(text):
            continue
        if base.REGION_PATTERNS[other].search(text):
            raise RuntimeError(f"cross-metro contamination in {region}: {rid} {text[:180]}")
        if base.END_RE.search(text) and "진행중" not in text:
            raise RuntimeError(f"ended row on current-only surface: {rid} {text[:180]}")
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


base.page_rows = page_rows

if __name__ == "__main__":
    raise SystemExit(asyncio.run(base.main()))
