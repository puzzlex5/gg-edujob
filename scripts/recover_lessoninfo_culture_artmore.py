#!/usr/bin/env python3
"""Recover Lessoninfo culture rows whose Lessoninfo detail route is cold-unsafe.

This is a second, fail-closed verification pass. For a culture:id:<N> row that failed because
Lessoninfo redirected away from its detail page, derive the corresponding ArtMore detail candidate
using the same numeric ID. Expose it only when a brand-new mobile browser context proves all of:

* requested and final ArtMore rec_idx == the Lessoninfo numeric stable ID
* final route is the ArtMore recruitment detail route (not a list/home/login page)
* HTTP/challenge checks pass
* the rendered body matches the expected posting title

Rows that cannot be proven remain non-clickable. culture:id:94673 is a permanent fail-closed
regression sentinel and is never eligible for fallback recovery.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "lessoninfo_jobs.json"
REPORT = ROOT / "lessoninfo_culture_link_report.json"
KST = timezone(timedelta(hours=9))
CONCURRENCY = max(1, int(os.getenv("LESSONINFO_CULTURE_FALLBACK_CONCURRENCY", "4")))
NAV_TIMEOUT_MS = int(os.getenv("LESSONINFO_CULTURE_FALLBACK_TIMEOUT_MS", "20000"))
MAX_ATTEMPTS = max(1, int(os.getenv("LESSONINFO_CULTURE_FALLBACK_ATTEMPTS", "3")))
KNOWN_BAD_IDS = {"culture:id:94673"}
ARTMORE_DETAIL_PATH = "/sub/recruit/search_view.do"
CHALLENGE_RE = re.compile(
    r"자동등록방지|보안절차|사람인지 확인|로봇이 아닙니다|verify you are human|captcha|access denied|cloudflare",
    re.I,
)
GENERIC_TITLE_TOKENS = {
    "채용", "공고", "모집", "직원", "강사", "기간제", "계약직", "정규직", "2026", "2025", "지원", "접수",
}


def now_kst() -> datetime:
    return datetime.now(KST)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def compact(value: str) -> str:
    value = re.sub(r"^\(?\s*복사\s*\)?\s*", "", value or "", flags=re.I)
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", value).lower()


def title_tokens(value: str) -> list[str]:
    tokens = [t.lower() for t in re.findall(r"[0-9A-Za-z가-힣]{2,}", value or "")]
    return [t for t in tokens if t not in GENERIC_TITLE_TOKENS]


def title_matches(expected: str, actual_text: str) -> bool:
    expected_compact = compact(expected)
    actual_compact = compact((actual_text or "")[:16000])
    if len(expected_compact) >= 6 and expected_compact in actual_compact:
        return True
    tokens = list(dict.fromkeys(title_tokens(expected)))
    if not tokens:
        return False
    hits = sum(1 for token in tokens if token in actual_compact)
    required = 1 if len(tokens) == 1 and len(tokens[0]) >= 5 else max(2, (len(tokens) + 1) // 2)
    return hits >= required


def stable_numeric_id(row: dict) -> str:
    match = re.fullmatch(r"culture:id:(\d+)", str(row.get("sourceIdentity") or ""))
    return match.group(1) if match else ""


def candidate_url(expected_id: str) -> str:
    return f"https://www.artmore.kr{subpath()}?rec_idx={expected_id}"


def subpath() -> str:
    return ARTMORE_DETAIL_PATH


def artmore_identity_matches(url: str, expected_id: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in {"artmore.kr", "www.artmore.kr"}:
            return False
        if parsed.path.lower() != ARTMORE_DETAIL_PATH.lower():
            return False
        actual = str((parse_qs(parsed.query).get("rec_idx") or [""])[0])
        return bool(expected_id) and actual == expected_id
    except Exception:
        return False


async def cold_context(browser):
    context = await browser.new_context(
        locale="ko-KR",
        timezone_id="Asia/Seoul",
        viewport={"width": 390, "height": 844},
        device_scale_factor=2,
        is_mobile=True,
        has_touch=True,
        user_agent=(
            "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"
        ),
    )
    await context.set_extra_http_headers({"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5"})
    return context


async def body_text(page) -> str:
    try:
        return await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


async def verify_once(browser, row: dict, attempt: int) -> tuple[bool, str, str]:
    expected_id = stable_numeric_id(row)
    url = candidate_url(expected_id)
    if not artmore_identity_matches(url, expected_id):
        return False, "artmore-request-id-mismatch", ""

    context = await cold_context(browser)
    page = await context.new_page()
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        try:
            await page.wait_for_load_state("networkidle", timeout=2500)
        except PlaywrightTimeoutError:
            pass
        text = await body_text(page)
        final_url = page.url
        if response is not None and response.status >= 400:
            return False, f"artmore-http-{response.status}", final_url
        if CHALLENGE_RE.search(text[:8000]):
            return False, "artmore-challenge", final_url
        if not artmore_identity_matches(final_url, expected_id):
            return False, "artmore-final-id-or-route-mismatch", final_url
        if not title_matches(str(row.get("title") or ""), text):
            return False, "artmore-title-mismatch", final_url
        return True, "cold-browser-artmore-id-title-match", final_url
    except Exception as exc:
        return False, f"artmore-navigation:{type(exc).__name__}", ""
    finally:
        await page.close()
        await context.close()


async def recover_row(browser, sem: asyncio.Semaphore, row: dict) -> tuple[dict, str]:
    async with sem:
        updated = dict(row)
        sid = str(row.get("sourceIdentity") or "")
        if sid in KNOWN_BAD_IDS:
            return updated, "sentinel-skipped"
        expected_id = stable_numeric_id(row)
        if not expected_id:
            return updated, "missing-stable-id"
        if row.get("detailLinkVerified") is not False:
            return updated, "not-pending"
        if str(row.get("detailLinkReason") or "") != "cold-browser-route-mismatch":
            return updated, "not-route-mismatch"

        last_reason = "artmore-not-verified"
        last_final = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            ok, reason, final_url = await verify_once(browser, row, attempt)
            last_reason, last_final = reason, final_url
            if ok:
                linked = [str(x) for x in (row.get("linkedOfficialUrls") or []) if x]
                if final_url not in linked:
                    linked.append(final_url)
                updated.update({
                    "detailLinkVerified": True,
                    "detailLinkReason": reason,
                    "detailLinkVerificationReason": reason,
                    "verifiedAt": now_kst().isoformat(timespec="seconds"),
                    "verifiedUrl": final_url,
                    "resolvedUrlType": "official",
                    "url": final_url,
                    "originalUrl": final_url,
                    "linkedOfficialUrls": linked,
                })
                return updated, "recovered"

            permanent = reason in {"artmore-request-id-mismatch", "artmore-final-id-or-route-mismatch"}
            if reason.startswith("artmore-http-"):
                try:
                    status = int(reason.rsplit("-", 1)[1])
                    permanent = permanent or (400 <= status < 500 and status not in {408, 425, 429})
                except Exception:
                    pass
            if permanent:
                break
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(0.3 * attempt)

        updated["artmoreFallbackReason"] = last_reason
        if last_final:
            updated["artmoreFallbackFinalUrl"] = last_final
        return updated, last_reason


async def run() -> int:
    payload = load_json(DATA, {"jobs": []})
    jobs = payload if isinstance(payload, list) else list(payload.get("jobs") or [])
    eligible = [
        row for row in jobs
        if row.get("sourceSurface") == "culture-arts"
        and row.get("detailLinkVerified") is False
        and str(row.get("detailLinkReason") or "") == "cold-browser-route-mismatch"
        and str(row.get("sourceIdentity") or "") not in KNOWN_BAD_IDS
        and stable_numeric_id(row)
    ]
    eligible_ids = {str(row.get("sourceIdentity") or "") for row in eligible}

    outcomes: dict[str, tuple[dict, str]] = {}
    if eligible:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
            sem = asyncio.Semaphore(CONCURRENCY)
            recovered = await asyncio.gather(*(recover_row(browser, sem, row) for row in eligible))
            await browser.close()
        outcomes = {
            str(row.get("sourceIdentity") or ""): result
            for row, result in zip(eligible, recovered)
        }

    output_jobs: list[dict] = []
    outcome_counts: dict[str, int] = {}
    for row in jobs:
        sid = str(row.get("sourceIdentity") or "")
        if sid in outcomes:
            updated, outcome = outcomes[sid]
            output_jobs.append(updated)
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        else:
            output_jobs.append(row)

    if isinstance(payload, list):
        output = output_jobs
    else:
        output = dict(payload)
        output["updatedAt"] = now_kst().strftime("%Y-%m-%d %H:%M KST")
        output["jobs"] = output_jobs
    write_json(DATA, output)

    culture = [row for row in output_jobs if row.get("sourceSurface") == "culture-arts"]
    verified = [row for row in culture if row.get("detailLinkVerified") is True]
    pending = [row for row in culture if row.get("detailLinkVerified") is False]
    missing = [row for row in culture if row.get("detailLinkVerified") not in {True, False}]
    reason_counts: dict[str, int] = {}
    for row in culture:
        reason = str(row.get("detailLinkReason") or "missing")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    report = load_json(REPORT, {})
    report.update({
        "generatedAt": now_kst().isoformat(timespec="seconds"),
        "healthy": len(missing) == 0,
        "eligibleCultureRows": len(culture),
        "verifiedCultureRows": len(verified),
        "verifiedOfficialRows": sum(1 for row in verified if row.get("resolvedUrlType") == "official"),
        "verifiedLessoninfoRows": sum(1 for row in verified if row.get("resolvedUrlType") == "lessoninfo"),
        "unverifiedCultureRows": len(pending),
        "verificationMissingCount": len(missing),
        "verificationMissingExamples": [row.get("sourceIdentity") for row in missing[:20]],
        "reasonCounts": reason_counts,
        "artmoreFallbackAttemptedRows": len(eligible_ids),
        "artmoreFallbackRecoveredRows": outcome_counts.get("recovered", 0),
        "artmoreFallbackOutcomeCounts": outcome_counts,
        "artmoreFallbackPolicy": "exact-rec-idx+detail-route+title+cold-mobile-v1",
    })
    write_json(REPORT, report)

    summary = {
        "attempted": len(eligible_ids),
        "recovered": outcome_counts.get("recovered", 0),
        "verifiedAfterRecovery": len(verified),
        "pendingAfterRecovery": len(pending),
        "missing": len(missing),
        "outcomes": outcome_counts,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not missing else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
