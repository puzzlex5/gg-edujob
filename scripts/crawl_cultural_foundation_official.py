#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright

from cultural_foundation_common import KST, generated_at, is_recruitment_title, load_foundations, norm

OUT = Path("cultural_foundation_jobs.candidate.json")
REPORT = Path("cultural_foundation_official_report.json")
DATE_RE = re.compile(r"(20\d{2})[.\-/년\s]+(\d{1,2})[.\-/월\s]+(\d{1,2})")
BOARD_RE = re.compile(r"채용|인재|직원", re.I)
GENERIC_LABELS = {"채용", "채용공고", "채용정보", "직원채용", "인재채용", "더보기", "more"}
CONCURRENCY = 5
TIMEOUT = 25000


def root_host(url: str) -> str:
    host = (urlparse(str(url or "")).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def allowed_site(url: str, foundation: dict) -> bool:
    target = root_host(url)
    allowed = {root_host(foundation.get("homepage")), root_host(foundation.get("officialRecruitmentUrl"))}
    allowed.discard("")
    return bool(target and target in allowed)


def dates_from(text: str) -> list[str]:
    out = []
    for y, m, d in DATE_RE.findall(str(text or "")):
        try:
            out.append(date(int(y), int(m), int(d)).isoformat())
        except Exception:
            pass
    return out


def score_board(text: str, href: str) -> int:
    value = f"{text} {href}".lower()
    score = 0
    if "채용공고" in value: score += 10
    if "채용" in value: score += 6
    if "recruit" in value: score += 5
    if "직원" in value: score += 3
    if "notice" in value or "board" in value: score += 1
    return score


async def anchors(page):
    return await page.locator("a[href]").evaluate_all("""els=>els.map(a=>({text:(a.innerText||a.textContent||'').replace(/\s+/g,' ').trim(),href:a.href||'',outer:(a.closest('tr,li,article,div')?.innerText||'').replace(/\s+/g,' ').trim().slice(0,1200)}))""")


async def crawl_one(browser, sem: asyncio.Semaphore, foundation: dict):
    async with sem:
        context = await browser.new_context(locale="ko-KR", user_agent="Mozilla/5.0 (Linux; Android 15; Pixel 7) AppleWebKit/537.36 Chrome/151 Mobile Safari/537.36")
        page = await context.new_page()
        result = {
            "foundationRegistryId": foundation["id"], "foundationName": foundation["name"],
            "region": foundation["region"], "municipality": foundation["municipality"],
            "homepage": foundation["homepage"], "explicitRecruitmentUrl": foundation.get("officialRecruitmentUrl") or "",
            "status": "not-checked", "boardUrls": [], "jobs": [], "errors": []
        }
        board_candidates = []
        try:
            await page.goto(foundation["homepage"], wait_until="domcontentloaded", timeout=TIMEOUT)
            await page.wait_for_timeout(450)
            for a in await anchors(page):
                href, text = str(a.get("href") or ""), str(a.get("text") or "")
                if href.startswith("http") and allowed_site(href, foundation) and BOARD_RE.search(text + " " + href):
                    board_candidates.append((score_board(text, href), href, text))
            if foundation.get("officialRecruitmentUrl"):
                board_candidates.append((100, foundation["officialRecruitmentUrl"], "registry-explicit"))
        except Exception as exc:
            result["errors"].append(f"homepage:{type(exc).__name__}:{exc}")

        uniq = []
        seen = set()
        for score, href, text in sorted(board_candidates, reverse=True):
            key = href.split("#", 1)[0]
            if key in seen: continue
            seen.add(key); uniq.append((score, key, text))
        # If no dedicated board link is discoverable, inspect the homepage itself. This gives a
        # coverage outcome without ever treating the homepage URL as a job detail.
        if not uniq:
            uniq = [(0, foundation["homepage"], "homepage-fallback")]
        uniq = uniq[:4]
        result["boardUrls"] = [x[1] for x in uniq]
        today = datetime.now(KST).date()
        for _, board_url, _ in uniq:
            try:
                await page.goto(board_url, wait_until="domcontentloaded", timeout=TIMEOUT)
                await page.wait_for_timeout(450)
                for a in await anchors(page):
                    title = str(a.get("text") or "").strip()
                    href = str(a.get("href") or "").split("#", 1)[0]
                    if not title or norm(title) in {norm(x) for x in GENERIC_LABELS}:
                        continue
                    if not is_recruitment_title(title):
                        continue
                    if not href.startswith("http") or href == board_url or not allowed_site(href, foundation):
                        continue
                    block = str(a.get("outer") or "")
                    ds = dates_from(block + " " + title)
                    registered = ds[0] if ds else ""
                    apply_end = ds[-1] if len(ds) >= 2 else ""
                    if apply_end and apply_end < today.isoformat():
                        continue
                    if not apply_end and registered:
                        try:
                            if date.fromisoformat(registered) < today - timedelta(days=90):
                                continue
                        except Exception:
                            pass
                    result["jobs"].append({
                        "foundationRegistryId": foundation["id"], "foundationName": foundation["name"],
                        "region": foundation["region"], "municipality": foundation["municipality"],
                        "title": title, "registered": registered, "applyEnd": apply_end,
                        "candidateUrl": href, "boardUrl": board_url,
                    })
            except Exception as exc:
                result["errors"].append(f"board:{board_url}:{type(exc).__name__}:{exc}")
        by_url = {x["candidateUrl"]: x for x in result["jobs"]}
        result["jobs"] = list(by_url.values())
        if result["jobs"]:
            result["status"] = "jobs-discovered"
        elif result["boardUrls"]:
            result["status"] = "board-checked-no-current-job"
        elif result["errors"]:
            result["status"] = "fetch-error"
        else:
            result["status"] = "no-board-found"
        await context.close()
        return result


async def main_async() -> int:
    foundations = load_foundations()
    sem = asyncio.Semaphore(CONCURRENCY)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        results = await asyncio.gather(*(crawl_one(browser, sem, f) for f in foundations))
        await browser.close()
    jobs = []
    for r in results:
        jobs.extend(r.pop("jobs"))
    payload = {"generatedAt": generated_at(), "source": "문화재단 공식홈페이지", "role": "primary-candidate", "jobs": jobs}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    coverage = {r["foundationRegistryId"]: r for r in results}
    missing = [f["id"] for f in foundations if f["id"] not in coverage]
    report = {
        "generatedAt": generated_at(), "source": "문화재단 공식홈페이지",
        "healthy": len(results) == len(foundations) and not missing,
        "traversalComplete": len(results) == len(foundations) and not missing,
        "foundationCount": len(foundations), "coverageOutcomeCount": len(results), "missingCoverageOutcomes": missing,
        "candidateJobs": len(jobs),
        "statusCounts": {s: sum(1 for r in results if r["status"] == s) for s in sorted({r["status"] for r in results})},
        "institutions": results,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("healthy", "foundationCount", "coverageOutcomeCount", "candidateJobs", "statusCounts", "missingCoverageOutcomes")}, ensure_ascii=False, indent=2))
    return 0 if report["healthy"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
