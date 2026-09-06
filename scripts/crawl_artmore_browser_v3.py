#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime

import crawl_artmore_browser as base

NEXT_RE = re.compile(r"(?:다음|next|›|»)", re.I)
D_RE = re.compile(r"\bD-(\d+)\b", re.I)
PAGINATION_SELECTORS = ("#paging", ".paging", ".pagination")


def deadline_signal(apply_end: str, raw_text: str) -> bool:
    """True only when the row itself signals an unexpired/future deadline."""
    if D_RE.search(raw_text or ""):
        return True
    if not apply_end:
        return False
    try:
        return date.fromisoformat(apply_end) >= base.TODAY
    except ValueError:
        return False


async def raw_ids(page) -> list[str]:
    hrefs = await page.locator('a[href*="rec_idx="]').evaluate_all(
        "els=>els.map(a=>a.getAttribute('href')||'')"
    )
    out: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        m = base.REC_RE.search(href)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(m.group(1))
    return out


async def page_state(page) -> dict:
    """Read only pagination controls from a dedicated pagination container.

    A missing/unrecognised next control is deliberately represented as null and
    can never be used as completion proof.
    """
    return await page.evaluate(
        """(selectors)=>{
          const visible = e => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
          let root=null, selector=null;
          for (const s of selectors) {
            const candidates=[...document.querySelectorAll(s)].filter(visible);
            if (candidates.length) { root=candidates[0]; selector=s; break; }
          }
          const pageInput=document.querySelector('#page');
          const currentPage=pageInput && /^\\d+$/.test(pageInput.value||'') ? Number(pageInput.value) : null;
          const body=(document.body?.innerText||'');
          const renderEvidence=body.includes('채용정보 목록') && !!pageInput;
          if (!root) return {paginationContainer:null,currentPage,hasNextEnabled:null,renderEvidence};
          const controls=[...root.querySelectorAll('a,button')];
          const next=controls.find(e=>{
            const text=[e.innerText,e.textContent,e.getAttribute('aria-label'),e.getAttribute('title'),e.className]
              .filter(Boolean).join(' ').replace(/\\s+/g,' ').trim();
            return /(?:다음|next|›|»)/i.test(text);
          }) || null;
          if (!next) return {paginationContainer:selector,currentPage,hasNextEnabled:null,renderEvidence};
          const cls=String(next.className||'');
          const disabled=next.hasAttribute('disabled') || next.getAttribute('aria-disabled')==='true' || /(?:^|\\s)disabled(?:\\s|$)/i.test(cls);
          return {paginationContainer:selector,currentPage,hasNextEnabled:!disabled,renderEvidence};
        }""",
        list(PAGINATION_SELECTORS),
    )


async def parse_rows(page, region: str) -> tuple[list[dict], list[dict], list[str]]:
    anchors = page.locator('a[href*="rec_idx="]')
    accepted: list[dict] = []
    ambiguous: list[dict] = []
    raw: list[str] = []
    seen: set[str] = set()
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
        raw.append(rid)
        title = re.sub(r"\s+", " ", (await a.inner_text()).strip())
        text = await a.evaluate(
            """a=>{
              let n=a, best=(a.innerText||'').replace(/\s+/g,' ').trim();
              const target=(a.getAttribute('href')||'').match(/rec_idx=(\d+)/)?.[1];
              for(let i=0;i<9&&n;i++,n=n.parentElement){
                const t=(n.innerText||'').replace(/\s+/g,' ').trim();
                const ids=[...n.querySelectorAll('a[href*="rec_idx="]')]
                  .map(x=>(x.getAttribute('href')||'').match(/rec_idx=(\d+)/))
                  .filter(Boolean).map(x=>x[1]);
                const uniq=[...new Set(ids)];
                if(uniq.length===1 && uniq[0]===target && t.length>best.length && t.length<1400){best=t;continue;}
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
        in_region = bool(base.REGION_PATTERNS[region].search(text))
        cross_region = bool(base.REGION_PATTERNS[other].search(text))
        ended_label = bool(base.END_RE.search(text) and "진행중" not in text)
        future_signal = deadline_signal(apply_end, text)
        if cross_region:
            raise RuntimeError(f"cross-metro contamination in {region}: {rid} {text[:180]}")
        if not in_region:
            ambiguous.append({
                "stableId": rid,
                "reason": "region-filtered",
                "hasFutureDeadlineSignal": future_signal,
                "applyEnd": apply_end,
                "rawListText": text,
                "url": base.urljoin(base.URL, href),
            })
            continue
        if ended_label:
            ambiguous.append({
                "stableId": rid,
                "reason": "ended-label",
                "hasFutureDeadlineSignal": future_signal,
                "applyEnd": apply_end,
                "rawListText": text,
                "url": base.urljoin(base.URL, href),
            })
            continue
        accepted.append({
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
    return accepted, ambiguous, raw


async def collect_surface(browser, region: str, code: str):
    page = await browser.new_page(locale="ko-KR")
    try:
        expected = await base.establish_filter(page, region, code)
        out: list[dict] = []
        ambiguous_rows: list[dict] = []
        page_reports: list[dict] = []
        seen_raw: set[str] = set()
        seen_accepted: set[str] = set()
        max_pages = int(__import__("os").environ.get("ARTMORE_MAX_PAGES", "250"))
        termination_reason = None
        traversal_complete = False

        for n in range(1, max_pages + 1):
            changed = True
            if n > 1:
                changed = await base.goto_page(page, n)
            body = await page.locator("body").inner_text()
            if base.BLOCK_RE.search(body):
                termination_reason = "block_page"
                break
            area_vals = await page.locator('input[name="array_area_type"]').evaluate_all("els=>els.map(e=>e.value)")
            current = await page.locator("#exclude_end_yn").input_value() if await page.locator("#exclude_end_yn").count() else ""
            if expected not in area_vals or current != "Y":
                termination_reason = "filter_drift"
                break

            state = await page_state(page)
            rows, ambiguous, raw = await parse_rows(page, region)
            raw_new = [x for x in raw if x not in seen_raw]
            page_reports.append({
                "page": n,
                "requestedPage": n,
                "currentPage": state.get("currentPage"),
                "paginationContainer": state.get("paginationContainer"),
                "hasNextEnabled": state.get("hasNextEnabled"),
                "rawRowCount": len(raw),
                "rawNewIdCount": len(raw_new),
                "acceptedRowCount": len(rows),
                "ambiguousRowCount": len(ambiguous),
                "firstRawId": raw[0] if raw else None,
                "lastRawId": raw[-1] if raw else None,
                "navigationChanged": changed,
            })

            if not raw:
                if state.get("renderEvidence") and state.get("currentPage") == n:
                    termination_reason = "no_raw_rows_confirmed"
                    traversal_complete = True
                else:
                    termination_reason = "blank_unproven"
                break
            if n > 1 and not changed and not raw_new:
                termination_reason = "navigation_stuck"
                break
            if not raw_new:
                termination_reason = "repeated_page"
                break

            seen_raw.update(raw_new)
            ambiguous_rows.extend(ambiguous)
            for row in rows:
                if row["stableId"] not in seen_accepted:
                    seen_accepted.add(row["stableId"])
                    out.append(row)

            if state.get("hasNextEnabled") is False:
                termination_reason = "pagination_exhausted"
                traversal_complete = True
                break

        if termination_reason is None:
            termination_reason = "safety_cap"
        ended = [x for x in ambiguous_rows if x.get("reason") == "ended-label"]
        conflicts = [x for x in ended if x.get("hasFutureDeadlineSignal")]
        total_raw = len(seen_raw)
        ended_ratio = len(ended) / total_raw if total_raw else 0.0
        conflict_ratio = len(conflicts) / total_raw if total_raw else 0.0
        quarantine_excessive = total_raw > 0 and len(ended) >= 5 and ended_ratio >= 0.5
        return out, {
            "region": region,
            "code": code,
            "pagesVisited": len(page_reports),
            "ids": len(out),
            "rawIdCount": total_raw,
            "rawIds": sorted(seen_raw, key=int, reverse=True),
            "terminationReason": termination_reason,
            "traversalComplete": traversal_complete,
            "pages": page_reports,
            "ambiguousRows": ambiguous_rows,
            "endedLabelQuarantineCount": len(ended),
            "endedLabelQuarantineRatio": round(ended_ratio, 6),
            "futureConflictCount": len(conflicts),
            "futureConflictRatio": round(conflict_ratio, 6),
            "quarantineExcessive": quarantine_excessive,
        }
    finally:
        await page.close()


async def main():
    generated = datetime.now(base.KST).isoformat(timespec="seconds")
    all_jobs: list[dict] = []
    surfaces: list[dict] = []
    errors: list[dict] = []
    async with base.async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            for region, code in base.REGIONS:
                try:
                    jobs, surface = await collect_surface(browser, region, code)
                    all_jobs.extend(jobs)
                    surfaces.append(surface)
                    if not surface.get("traversalComplete"):
                        errors.append({"region": region, "error": f"unproven traversal termination: {surface.get('terminationReason')}"})
                    if surface.get("quarantineExcessive"):
                        errors.append({"region": region, "error": "ended-label quarantine ratio indicates broken current-only filter"})
                except Exception as exc:
                    errors.append({"region": region, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            await browser.close()

    by_id = {j["sourceIdentity"]: j for j in all_jobs}
    all_jobs = list(by_id.values())
    discovered = set(by_id)
    raw_union = {f"artmore:{rid}" for s in surfaces for rid in (s.get("rawIds") or [])}
    prev = base.previous_ids()
    missing_after: list[str] = []
    traversal_complete = len(surfaces) == 2 and all(s.get("traversalComplete") for s in surfaces)
    healthy = traversal_complete and not errors and bool(all_jobs) and not missing_after
    if prev and len(raw_union) < max(10, int(len(prev) * 0.35)):
        healthy = False
        errors.append({"region": "all", "error": f"suspicious raw-ID drop: previous={len(prev)} rawCurrent={len(raw_union)}"})

    ledger = {
        "generatedAt": generated,
        "source": "아트모아",
        "mode": "candidate-isolated-proof-pagination-v3",
        "rawIds": sorted(raw_union),
        "entries": {sid: {"stableId": j["stableId"], "province": j["province"], "url": j["originalUrl"], "presentInLatestScan": True} for sid, j in by_id.items()},
    }
    report = {
        "generatedAt": generated,
        "source": "아트모아",
        "policy": "artmore-active-browser-v3-candidate",
        "publicationEnabled": False,
        "healthy": healthy,
        "traversalComplete": traversal_complete,
        "candidateIdCount": len(discovered),
        "rawIdCount": len(raw_union),
        "publishedIdCount": len(discovered),
        "missingAfterCount": len(missing_after),
        "missingAfter": missing_after,
        "sourceReports": surfaces,
        "ambiguousRows": [dict(x, region=s.get("region")) for s in surfaces for x in (s.get("ambiguousRows") or [])],
        "errors": errors,
        "regionsAllowed": ["서울", "경기"],
        "nextGate": "live sentinel detail validation + candidate promotion + unified search validation",
    }
    state = {
        "generatedAt": generated,
        "source": "아트모아",
        "healthyCandidate": healthy,
        "traversalComplete": traversal_complete,
        "candidateJobs": len(all_jobs),
        "rawIds": len(raw_union),
        "previousLedgerIds": len(prev),
        "preservedPublishedDataset": True,
    }
    base.OUT.write_text(json.dumps({"generatedAt": generated, "source": "아트모아", "jobs": all_jobs}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    base.LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    base.REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    base.STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"healthy": healthy, "jobs": len(all_jobs), "rawIds": len(raw_union), "surfaces": [{"region": s["region"], "terminationReason": s["terminationReason"], "traversalComplete": s["traversalComplete"], "ids": s["ids"], "rawIdCount": s["rawIdCount"], "futureConflictCount": s["futureConflictCount"]} for s in surfaces], "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if healthy else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
