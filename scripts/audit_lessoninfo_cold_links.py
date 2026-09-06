#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "lessoninfo_jobs.json"
OUT = ROOT / "lessoninfo_cold_link_audit.json"
KST = timezone(timedelta(hours=9))
CONCURRENCY = int(os.getenv("LESSONINFO_COLD_AUDIT_CONCURRENCY", "8"))
MOBILE_UA = "Mozilla/5.0 (Linux; Android 14; SM-S928N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"

NOISE_PREFIX_RE = re.compile(r"^(?:\s*\((?:복사|추가|재업|재등록)\)\s*)+", re.I)
# Explicit title shorthand only. Do not treat 임용/근무/공연 dates as application deadlines.
TITLE_DEADLINE_RE = re.compile(
    r"(?:~|～|마감\s*[:：]?\s*)(?:\s*)(?:(20\d{2}|\d{2})[.\-/])?(\d{1,2})[.\-/](\d{1,2})(?!\s*(?:임용|근무|공연|시작))",
    re.I,
)


def now_kst() -> datetime:
    return datetime.now(KST)


def norm(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", (s or "").lower())


def title_key(title: str) -> str:
    return norm(NOISE_PREFIX_RE.sub("", title or ""))


def load_jobs() -> list[dict]:
    d = json.loads(DATA.read_text(encoding="utf-8"))
    return d if isinstance(d, list) else d.get("jobs", [])


def identity_expected(job: dict) -> tuple[str, str, str]:
    raw = str(job.get("url") or job.get("originalUrl") or "")
    p = urlparse(raw)
    q = parse_qs(p.query)
    if job.get("sourceSurface") == "culture-arts":
        return "id", str((q.get("id") or [""])[0]), "/culture-jobs/detail.php"
    return "wr_no", str((q.get("wr_no") or [""])[0]), "/board/board.php"


def final_identity_matches(job: dict, final_url: str) -> bool:
    key, expected, path = identity_expected(job)
    if not expected:
        return False
    p = urlparse(final_url or "")
    q = parse_qs(p.query)
    return p.path.endswith(path) and str((q.get(key) or [""])[0]) == expected


def title_deadline(title: str, registered: str = "") -> str:
    # Ignore a bare parenthesized appointment date such as '(26.10.01.임용)'.
    m = TITLE_DEADLINE_RE.search(title or "")
    if not m:
        return ""
    year_raw, month_raw, day_raw = m.groups()
    try:
        ref = datetime.strptime(registered, "%Y-%m-%d").replace(tzinfo=KST) if registered else now_kst()
    except ValueError:
        ref = now_kst()
    year = ref.year
    if year_raw:
        year = int(year_raw)
        if year < 100:
            year += 2000
    try:
        dt = datetime(year, int(month_raw), int(day_raw), tzinfo=KST)
    except ValueError:
        return ""
    if not year_raw:
        if dt < ref - timedelta(days=300):
            dt = dt.replace(year=year + 1)
        elif dt > ref + timedelta(days=120):
            dt = dt.replace(year=year - 1)
    return dt.strftime("%Y-%m-%d")


async def audit_one(browser, sem: asyncio.Semaphore, job: dict) -> dict:
    async with sem:
        ctx = await browser.new_context(
            locale="ko-KR", timezone_id="Asia/Seoul",
            viewport={"width": 412, "height": 915}, user_agent=MOBILE_UA,
            is_mobile=True, has_touch=True,
        )
        page = await ctx.new_page()
        try:
            await page.route(
                re.compile(r".*"),
                lambda route: route.abort() if route.request.resource_type in {"image", "font", "media"} else route.continue_(),
            )
            configured = str(job.get("url") or job.get("originalUrl") or "")
            resp = await page.goto(configured, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=2500)
            except PlaywrightTimeoutError:
                pass
            try:
                body = await page.locator("body").inner_text(timeout=4000)
            except Exception:
                body = ""
            key = title_key(str(job.get("title") or ""))
            body_key = norm(body)
            id_match = final_identity_matches(job, page.url)
            content_match = bool(key) and key in body_key
            exact = id_match and content_match
            deadline = title_deadline(str(job.get("title") or ""), str(job.get("registered") or ""))
            expired_title = False
            if deadline:
                try:
                    expired_title = datetime.strptime(deadline, "%Y-%m-%d").date() < now_kst().date()
                except ValueError:
                    pass
            return {
                "sourceIdentity": job.get("sourceIdentity"),
                "sourceSurface": job.get("sourceSurface"),
                "title": job.get("title"),
                "registered": job.get("registered"),
                "configuredUrl": configured,
                "finalUrl": page.url,
                "httpStatus": resp.status if resp else None,
                "identityMatch": id_match,
                "contentMatch": content_match,
                "detailLinkVerified": exact,
                "reason": "exact" if exact else ("redirect-or-identity-mismatch" if not id_match else "content-mismatch"),
                "titleDeadline": deadline,
                "expiredByTitleSignal": expired_title,
                "bodyPrefix": re.sub(r"\s+", " ", body)[:450] if not exact else "",
            }
        except Exception as e:
            return {
                "sourceIdentity": job.get("sourceIdentity"),
                "sourceSurface": job.get("sourceSurface"),
                "title": job.get("title"),
                "registered": job.get("registered"),
                "configuredUrl": job.get("url") or job.get("originalUrl"),
                "finalUrl": page.url,
                "detailLinkVerified": False,
                "reason": "error",
                "error": f"{type(e).__name__}:{e}",
                "titleDeadline": title_deadline(str(job.get("title") or ""), str(job.get("registered") or "")),
            }
        finally:
            await ctx.close()


async def main() -> int:
    jobs = load_jobs()
    sem = asyncio.Semaphore(max(1, CONCURRENCY))
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        results = await asyncio.gather(*(audit_one(browser, sem, j) for j in jobs))
        await browser.close()

    per_surface = {}
    for r in results:
        s = str(r.get("sourceSurface") or "unknown")
        x = per_surface.setdefault(s, {"tested": 0, "exact": 0, "failed": 0, "expiredByTitleSignal": 0})
        x["tested"] += 1
        if r.get("detailLinkVerified"):
            x["exact"] += 1
        else:
            x["failed"] += 1
        if r.get("expiredByTitleSignal"):
            x["expiredByTitleSignal"] += 1

    failures = [r for r in results if not r.get("detailLinkVerified")]
    expired = [r for r in results if r.get("expiredByTitleSignal")]
    report = {
        "generatedAt": now_kst().isoformat(timespec="seconds"),
        "policy": "lessoninfo-cold-mobile-content-bound-v1",
        "tested": len(results),
        "exact": len(results) - len(failures),
        "failed": len(failures),
        "perSurface": per_surface,
        "expiredByTitleSignalCount": len(expired),
        "failureExamples": failures[:40],
        "expiredTitleExamples": expired[:40],
        "results": results,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
