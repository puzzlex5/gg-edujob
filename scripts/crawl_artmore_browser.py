#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
URL = "https://www.artmore.kr/sub/recruit/search_list.do"
OUT = Path("artmore_jobs.candidate.json")
LEDGER = Path("artmore_source_id_ledger.candidate.json")
REPORT = Path("artmore_reconciliation_report.candidate.json")
STATE = Path("artmore_collection_state.candidate.json")
REGIONS = [("서울", "2001"), ("경기", "2083")]
REC_RE = re.compile(r"rec_idx=(\d+)")
DATE_RE = re.compile(r"20\d{2}[.-]\d{1,2}[.-]\d{1,2}")
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한", re.I)
END_RE = re.compile(r"\b마감\b|\b종료\b|채용완료", re.I)
REGION_PATTERNS = {
    "서울": re.compile(r"(?<![가-힣])서울(?:특별시)?\s"),
    "경기": re.compile(r"(?<![가-힣])경기(?:도)?\s"),
}


def iso_date(raw: str) -> str:
    try:
        y, m, d = [int(x) for x in re.split(r"[.-]", raw)]
        return f"{y:04d}-{m:02d}-{d:02d}"
    except Exception:
        return ""


async def visible_click(loc):
    for i in range(await loc.count()):
        try:
            if await loc.nth(i).is_visible():
                await loc.nth(i).click()
                return True
        except Exception:
            pass
    return False


async def enable_current_only(page):
    hidden = page.locator("#exclude_end_yn")
    cur = page.locator("#jobs_ving_chk")
    if not await hidden.count() or not await cur.count():
        raise RuntimeError("current-only controls missing")
    if await hidden.input_value() == "Y":
        return
    if await cur.is_checked():
        await cur.evaluate("e=>{e.checked=false}")
    if not await page.evaluate("()=>typeof window.jQuery==='function'"):
        raise RuntimeError("jQuery unavailable for current-only handler")
    await page.evaluate("""()=>{const $=window.jQuery,$c=$('#jobs_ving_chk');$c.prop('checked',false);$c.trigger('click');}""")
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception:
        pass
    await page.wait_for_timeout(600)
    if await page.locator("#exclude_end_yn").input_value() != "Y":
        raise RuntimeError("current-only handler failed")


async def establish_filter(page, region: str, code: str):
    await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(700)
    body = await page.locator("body").inner_text()
    if BLOCK_RE.search(body):
        raise RuntimeError("human-check/block page detected")
    if not await visible_click(page.get_by_role("button", name="지역 선택")):
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
    if not await visible_click(page.locator("#btn_area_ok")):
        raise RuntimeError("region confirm button missing")
    await page.wait_for_timeout(350)
    vals = await page.locator('input[name="array_area_type"]').evaluate_all("els=>els.map(e=>e.value)")
    if expected not in vals:
        raise RuntimeError(f"area selector not committed: {expected}; got={vals}")
    await enable_current_only(page)
    vals = await page.locator('input[name="array_area_type"]').evaluate_all("els=>els.map(e=>e.value)")
    if expected not in vals:
        raise RuntimeError("area filter lost after current-only submit")
    return expected


async def page_rows(page, region: str):
    anchors = page.locator('a[href*="rec_idx="]')
    rows = []
    seen = set()
    for i in range(await anchors.count()):
        a = anchors.nth(i)
        href = await a.get_attribute("href") or ""
        m = REC_RE.search(href)
        if not m:
            continue
        rid = m.group(1)
        if rid in seen:
            continue
        seen.add(rid)
        title = re.sub(r"\s+", " ", (await a.inner_text()).strip())
        text = await a.evaluate("""a=>{let n=a,b='';for(let i=0;i<8&&n;i++,n=n.parentElement){const t=(n.innerText||'').replace(/\s+/g,' ').trim();if(t.length>b.length&&t.length<1400)b=t}return b}""")
        dates = DATE_RE.findall(text)
        registered = iso_date(dates[0]) if dates else ""
        apply_end = iso_date(dates[-1]) if len(dates) >= 2 else ""
        location = ""
        if region == "서울":
            lm = re.search(r"서울(?:특별시)?\s+[^\s]+(?:구|군)(?:\s+[^\s]+){0,4}", text)
        else:
            lm = re.search(r"경기(?:도)?\s+[^\s]+(?:시|군)(?:\s+[^\s]+){0,4}", text)
        if lm:
            location = lm.group(0).strip()
        other = "경기" if region == "서울" else "서울"
        if not REGION_PATTERNS[region].search(text):
            continue
        if REGION_PATTERNS[other].search(text):
            raise RuntimeError(f"cross-metro contamination in {region}: {rid} {text[:180]}")
        if END_RE.search(text) and "진행중" not in text:
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
            "originalUrl": urljoin(URL, href),
            "url": urljoin(URL, href),
            "linkedOfficialUrls": [],
            "rawListText": text,
        })
    return rows


async def goto_page(page, number: int):
    before = await page.locator('a[href*="rec_idx="]').evaluate_all("els=>els.slice(0,15).map(a=>a.href).join('|')")
    if await page.evaluate("()=>typeof window.goPageNavigation==='function'"):
        try:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                await page.evaluate("n=>window.goPageNavigation(n)", number)
        except Exception:
            await page.wait_for_timeout(1200)
    else:
        p = page.locator("#page")
        if not await p.count():
            raise RuntimeError("no pagination function or #page input")
        await p.evaluate("(e,n)=>e.value=String(n)", number)
        form = page.locator("form").filter(has=page.locator("#page")).first
        if not await form.count():
            raise RuntimeError("pagination form missing")
        await form.evaluate("f=>f.submit()")
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
    await page.wait_for_timeout(500)
    after = await page.locator('a[href*="rec_idx="]').evaluate_all("els=>els.slice(0,15).map(a=>a.href).join('|')")
    return before != after


async def collect_surface(browser, region: str, code: str):
    page = await browser.new_page(locale="ko-KR")
    try:
        expected = await establish_filter(page, region, code)
        out = []
        page_reports = []
        seen_ids = set()
        max_pages = int(__import__("os").environ.get("ARTMORE_MAX_PAGES", "250"))
        for n in range(1, max_pages + 1):
            if n > 1:
                changed = await goto_page(page, n)
                if not changed:
                    break
            body = await page.locator("body").inner_text()
            if BLOCK_RE.search(body):
                raise RuntimeError(f"human-check/block on page {n}")
            area_vals = await page.locator('input[name="array_area_type"]').evaluate_all("els=>els.map(e=>e.value)")
            current = await page.locator("#exclude_end_yn").input_value() if await page.locator("#exclude_end_yn").count() else ""
            if expected not in area_vals or current != "Y":
                raise RuntimeError(f"filter drift page={n}: area={area_vals} current={current}")
            rows = await page_rows(page, region)
            ids = [r["stableId"] for r in rows]
            new_ids = [x for x in ids if x not in seen_ids]
            page_reports.append({"page": n, "rowCount": len(rows), "newIdCount": len(new_ids), "firstId": ids[0] if ids else None, "lastId": ids[-1] if ids else None})
            if not rows or not new_ids:
                break
            for r in rows:
                if r["stableId"] not in seen_ids:
                    seen_ids.add(r["stableId"])
                    out.append(r)
            if len(rows) < 10:
                break
        if len(page_reports) >= max_pages and page_reports[-1].get("newIdCount"):
            raise RuntimeError(f"max page safety cap reached for {region}: {max_pages}")
        if not out:
            raise RuntimeError(f"zero rows for {region}")
        return out, page_reports
    finally:
        await page.close()


def previous_ids():
    for path in (Path("artmore_source_id_ledger.json"), LEDGER):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            entries = d.get("entries", {}) if isinstance(d, dict) else {}
            return set(entries)
        except Exception:
            pass
    return set()


async def main():
    generated = datetime.now(KST).isoformat(timespec="seconds")
    all_jobs = []
    surfaces = []
    errors = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        for region, code in REGIONS:
            try:
                jobs, pages = await collect_surface(browser, region, code)
                all_jobs.extend(jobs)
                surfaces.append({"region": region, "code": code, "pagesVisited": len(pages), "ids": len(jobs), "pages": pages})
            except Exception as exc:
                errors.append({"region": region, "error": f"{type(exc).__name__}: {exc}"})
        await browser.close()

    by_id = {j["sourceIdentity"]: j for j in all_jobs}
    all_jobs = list(by_id.values())
    discovered = set(by_id)
    prev = previous_ids()
    missing_after = []  # candidate dataset is built from the complete discovered set in this run
    healthy = len(surfaces) == 2 and not errors and bool(all_jobs) and not missing_after
    if prev and len(discovered) < max(10, int(len(prev) * 0.35)):
        healthy = False
        errors.append({"region": "all", "error": f"suspicious drop: previous={len(prev)} current={len(discovered)}"})

    ledger = {
        "generatedAt": generated,
        "source": "아트모아",
        "mode": "candidate-isolated",
        "entries": {sid: {"stableId": j["stableId"], "province": j["province"], "url": j["originalUrl"], "presentInLatestScan": True} for sid, j in by_id.items()},
    }
    report = {
        "generatedAt": generated,
        "source": "아트모아",
        "policy": "artmore-active-browser-v1-candidate",
        "publicationEnabled": False,
        "healthy": healthy,
        "traversalComplete": healthy,
        "candidateIdCount": len(discovered),
        "publishedIdCount": len(discovered),
        "missingAfterCount": len(missing_after),
        "missingAfter": missing_after,
        "sourceReports": surfaces,
        "errors": errors,
        "regionsAllowed": ["서울", "경기"],
        "nextGate": "detail-link validation + candidate promotion + unified validation",
    }
    state = {
        "generatedAt": generated,
        "source": "아트모아",
        "healthyCandidate": healthy,
        "candidateJobs": len(all_jobs),
        "previousLedgerIds": len(prev),
        "preservedPublishedDataset": True,
    }
    OUT.write_text(json.dumps({"generatedAt": generated, "source": "아트모아", "jobs": all_jobs}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"healthy": healthy, "jobs": len(all_jobs), "surfaces": [{"region": x["region"], "pagesVisited": x["pagesVisited"], "ids": x["ids"]} for x in surfaces], "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if healthy else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
