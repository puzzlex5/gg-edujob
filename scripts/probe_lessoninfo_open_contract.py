#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "lessoninfo_jobs.json"
OUT = ROOT / "lessoninfo_open_contract_probe.json"

CULTURE_LIST = "https://www.lessoninfo.co.kr/culture-jobs/list.php"
AFTER_LIST = "https://www.lessoninfo.co.kr/board/board.php?board_code=20130529164321_7227&code=&bo_table=20170316171033_6494"
MOBILE_UA = "Mozilla/5.0 (Linux; Android 14; SM-S928N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"


def norm(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", (s or "").lower())


def load_jobs() -> list[dict]:
    d = json.loads(DATA.read_text(encoding="utf-8"))
    return d if isinstance(d, list) else d.get("jobs", [])


def identity_parts(job: dict) -> tuple[str, str]:
    u = urlparse(str(job.get("url") or job.get("originalUrl") or ""))
    q = parse_qs(u.query)
    if job.get("sourceSurface") == "culture-arts":
        return "id", str((q.get("id") or [""])[0])
    return "wr_no", str((q.get("wr_no") or [""])[0])


def url_identity_matches(job: dict, final_url: str) -> bool:
    key, expected = identity_parts(job)
    if not expected:
        return False
    u = urlparse(final_url)
    q = parse_qs(u.query)
    got = str((q.get(key) or [""])[0])
    if got != expected:
        return False
    if job.get("sourceSurface") == "culture-arts":
        return u.path.endswith("/culture-jobs/detail.php")
    return u.path.endswith("/board/board.php")


async def page_snapshot(page, job: dict) -> dict:
    try:
        page_title = await page.title()
    except Exception:
        page_title = ""
    try:
        body = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        body = ""
    try:
        headings = await page.locator("h1,h2,h3,.subject,.title").evaluate_all(
            "els => els.slice(0,20).map(e => (e.innerText || e.textContent || '').trim()).filter(Boolean)"
        )
    except Exception:
        headings = []
    try:
        culture_detail_links = await page.locator('a[href*="/culture-jobs/detail.php"]').count()
    except Exception:
        culture_detail_links = -1
    expected = norm(str(job.get("title") or ""))
    heading_norms = [norm(str(x)) for x in headings]
    heading_match = bool(expected) and any(
        expected in h or h in expected for h in heading_norms if len(h) >= max(8, int(len(expected) * 0.45))
    )
    body_match = bool(expected) and expected in norm(body)
    exact = url_identity_matches(job, page.url) and heading_match and body_match
    return {
        "finalUrl": page.url,
        "pageTitle": page_title,
        "headings": headings[:12],
        "cultureDetailLinkCount": culture_detail_links,
        "urlIdentityMatch": url_identity_matches(job, page.url),
        "headingMatch": heading_match,
        "bodyMatch": body_match,
        "exact": exact,
        "bodyPrefix": re.sub(r"\s+", " ", body)[:1000],
    }


async def new_context(browser):
    ctx = await browser.new_context(
        locale="ko-KR",
        timezone_id="Asia/Seoul",
        viewport={"width": 412, "height": 915},
        user_agent=MOBILE_UA,
        is_mobile=True,
        has_touch=True,
    )
    await ctx.set_extra_http_headers({"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5"})
    return ctx


async def goto_and_snapshot(browser, job: dict, seed: bool) -> dict:
    ctx = await new_context(browser)
    page = await ctx.new_page()
    try:
        if seed:
            seed_url = CULTURE_LIST if job.get("sourceSurface") == "culture-arts" else AFTER_LIST
            await page.goto(seed_url, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except PlaywrightTimeoutError:
                pass
        resp = await page.goto(str(job.get("url") or job.get("originalUrl") or ""), wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except PlaywrightTimeoutError:
            pass
        await page.wait_for_timeout(800)
        snap = await page_snapshot(page, job)
        snap["httpStatus"] = resp.status if resp else None
        snap["seeded"] = seed
        return snap
    except Exception as e:
        return {"seeded": seed, "exact": False, "error": f"{type(e).__name__}:{e}", "finalUrl": page.url}
    finally:
        await ctx.close()


async def click_probe(browser, job: dict) -> dict:
    ctx = await new_context(browser)
    page = await ctx.new_page()
    events: list[dict] = []
    record = False

    def on_request(req):
        nonlocal record
        if not record:
            return
        u = req.url
        if "lessoninfo" not in u:
            return
        try:
            post_data = req.post_data or ""
        except Exception:
            post_data = ""
        events.append({"method": req.method, "url": u, "postData": post_data[:2500]})

    page.on("request", on_request)
    try:
        list_url = CULTURE_LIST if job.get("sourceSurface") == "culture-arts" else AFTER_LIST
        await page.goto(list_url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except PlaywrightTimeoutError:
            pass
        await page.wait_for_timeout(500)

        key, value = identity_parts(job)
        locator = page.locator(f'a[href*="{key}={value}"]') if value else page.locator("a").filter(has_text=str(job.get("title") or ""))
        if await locator.count() == 0:
            title = str(job.get("title") or "")
            short = re.sub(r"\s+", " ", title).strip()[:24]
            locator = page.locator("a").filter(has_text=short)
        if await locator.count() == 0:
            return {"foundRow": False, "events": []}
        a = locator.first
        meta = await a.evaluate(
            """el => ({
              href: el.getAttribute('href') || '',
              absoluteHref: el.href || '',
              onclick: el.getAttribute('onclick') || '',
              outerHTML: el.outerHTML,
              parentHTML: (el.closest('form,tr,li,article,.item,.row') || el.parentElement)?.outerHTML || ''
            })"""
        )
        record = True
        try:
            await a.click(timeout=10000)
        except Exception as e:
            meta["clickError"] = f"{type(e).__name__}:{e}"
        await page.wait_for_timeout(2500)
        snap = await page_snapshot(page, job)
        return {"foundRow": True, "anchor": meta, "afterClick": snap, "events": events[-30:]}
    except Exception as e:
        return {"foundRow": False, "error": f"{type(e).__name__}:{e}", "events": events[-30:]}
    finally:
        await ctx.close()


async def main() -> int:
    jobs = load_jobs()
    by_id = {str(j.get("sourceIdentity") or ""): j for j in jobs}
    selected: list[dict] = []
    sentinel = by_id.get("culture:id:94673")
    if sentinel:
        selected.append(sentinel)
    culture_recent = [j for j in jobs if j.get("sourceSurface") == "culture-arts" and j is not sentinel][:3]
    after_recent = [j for j in jobs if j.get("sourceSurface") == "afterschool-nulbom"][:2]
    selected.extend(culture_recent)
    selected.extend(after_recent)

    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        for job in selected:
            cold = await goto_and_snapshot(browser, job, seed=False)
            seeded = await goto_and_snapshot(browser, job, seed=True)
            clicked = await click_probe(browser, job)
            results.append({
                "sourceIdentity": job.get("sourceIdentity"),
                "sourceSurface": job.get("sourceSurface"),
                "title": job.get("title"),
                "configuredUrl": job.get("url") or job.get("originalUrl"),
                "cold": cold,
                "seededDirect": seeded,
                "listClick": clicked,
                "classification": (
                    "cold-exact" if cold.get("exact") else
                    "session-or-click-dependent" if seeded.get("exact") or (clicked.get("afterClick") or {}).get("exact") else
                    "not-exact-even-seeded"
                ),
            })
        await browser.close()

    report = {
        "policy": "lessoninfo-open-contract-probe-v1",
        "tested": len(results),
        "coldExact": sum(1 for r in results if r["cold"].get("exact")),
        "seededExact": sum(1 for r in results if r["seededDirect"].get("exact")),
        "clickExact": sum(1 for r in results if (r["listClick"].get("afterClick") or {}).get("exact")),
        "results": results,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
