#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from cultural_foundation_common import generated_at, norm

IN = Path("cultural_foundation_jobs.candidate.json")
CRAWL_REPORT = Path("cultural_foundation_official_report.json")
OUT = Path("cultural_foundation_official_jobs.json")
REPORT = Path("cultural_foundation_official_link_report.json")
CONCURRENCY = 6
TIMEOUT = 22000


def site_root(url: str) -> str:
    host = (urlparse(str(url or "")).hostname or "").lower().removeprefix("www.")
    p = host.split(".")
    return ".".join(p[-2:]) if len(p) >= 2 else host


async def verify_one(browser, sem, row):
    async with sem:
        ctx = await browser.new_context(locale="ko-KR", user_agent="Mozilla/5.0 (Linux; Android 15; Pixel 7) AppleWebKit/537.36 Chrome/151 Mobile Safari/537.36")
        page = await ctx.new_page()
        out = dict(row)
        out.update({"detailLinkVerified": False, "detailLinkReason": "not-verified", "verifiedUrl": ""})
        try:
            requested = str(row.get("candidateUrl") or "")
            await page.goto(requested, wait_until="domcontentloaded", timeout=TIMEOUT)
            await page.wait_for_timeout(350)
            final = page.url.split("#", 1)[0]
            body = await page.locator("body").inner_text()
            expected = norm(row.get("title"))
            actual = norm(body)
            if not final.startswith("http"):
                out["detailLinkReason"] = "non-http-final"
            elif site_root(final) != site_root(requested):
                out["detailLinkReason"] = "cross-site-redirect"
            elif final.rstrip("/") == str(row.get("boardUrl") or "").rstrip("/"):
                out["detailLinkReason"] = "redirected-to-board"
            elif len(expected) < 5 or expected not in actual:
                out["detailLinkReason"] = "title-mismatch"
            else:
                out["detailLinkVerified"] = True
                out["detailLinkReason"] = "cold-mobile-official-title-match"
                out["verifiedUrl"] = final
        except Exception as exc:
            out["detailLinkReason"] = f"browser-error:{type(exc).__name__}"
        await ctx.close()
        return out


async def main_async() -> int:
    data = json.loads(IN.read_text(encoding="utf-8"))
    candidates = data.get("jobs", []) if isinstance(data, dict) else []
    crawl_report = json.loads(CRAWL_REPORT.read_text(encoding="utf-8"))
    sem = asyncio.Semaphore(CONCURRENCY)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        checked = await asyncio.gather(*(verify_one(browser, sem, row) for row in candidates))
        await browser.close()
    verified = []
    failures = []
    for row in checked:
        if row.get("detailLinkVerified") is True:
            url = str(row.get("verifiedUrl") or "")
            sid = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
            verified.append({
                "sourceIdentity": f"culturefoundation:{sid}",
                "source": "문화재단 공식채용",
                "sourceType": "공식기관 채용",
                "trustLevel": "공식원문",
                "category": "private-recruitment",
                "sourceSurface": "cultural-foundation-official",
                "sourceSurfaceLabel": "문화재단 공식 채용",
                "foundationRegistryId": row.get("foundationRegistryId"),
                "foundationName": row.get("foundationName"),
                "title": row.get("title"),
                "registered": row.get("registered") or "",
                "applyEnd": row.get("applyEnd") or "",
                "province": row.get("region") or "",
                "region": row.get("region") or "",
                "metroRegion": row.get("region") or "",
                "location": f"{row.get('region') or ''} {row.get('municipality') or ''}".strip(),
                "url": url,
                "originalUrl": url,
                "verifiedUrl": url,
                "detailLinkVerified": True,
                "detailLinkReason": row.get("detailLinkReason"),
                "verifiedAt": generated_at(),
            })
        else:
            failures.append({"foundationRegistryId": row.get("foundationRegistryId"), "title": row.get("title"), "candidateUrl": row.get("candidateUrl"), "reason": row.get("detailLinkReason")})
    report = {
        "generatedAt": generated_at(),
        "source": "문화재단 공식채용",
        "publicationEnabled": bool(crawl_report.get("healthy")),
        "healthy": bool(crawl_report.get("healthy")),
        "traversalComplete": bool(crawl_report.get("traversalComplete")),
        "coverageOutcomeCount": crawl_report.get("coverageOutcomeCount"),
        "candidateJobs": len(candidates),
        "verifiedJobs": len(verified),
        "failedCandidates": len(failures),
        "verificationMissingCount": 0,
        "missingAfterCount": 0,
        "failures": failures[:100],
    }
    OUT.write_text(json.dumps({"generatedAt": generated_at(), "source": "문화재단 공식채용", "jobs": verified}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("healthy", "coverageOutcomeCount", "candidateJobs", "verifiedJobs", "failedCandidates", "verificationMissingCount")}, ensure_ascii=False, indent=2))
    return 0 if report["healthy"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
