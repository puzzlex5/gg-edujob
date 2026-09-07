#!/usr/bin/env python3
"""Cold-browser verification for Lessoninfo culture-arts links.

The regular Lessoninfo collector is intentionally allowed to preserve list-visible culture jobs even
when the list-seeded detail route is not safe to expose. This verifier is the acceptance boundary for
clickability: every current culture row is opened from a brand-new browser context with no cookies or
storage, then bound to the requested Lessoninfo ID and title. Verified official source links are
preferred when they can also be proven from a separate cold context.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "lessoninfo_jobs.json"
REPORT = ROOT / "lessoninfo_culture_link_report.json"
KST = timezone(timedelta(hours=9))
CONCURRENCY = max(1, int(os.getenv("LESSONINFO_CULTURE_COLD_CONCURRENCY", "4")))
NAV_TIMEOUT_MS = int(os.getenv("LESSONINFO_CULTURE_COLD_TIMEOUT_MS", "18000"))
KNOWN_BAD_COLD_IDS = {"culture:id:94673"}

LIST_PATHS = {"/culture-jobs/list.php", "/culture-jobs/", "/culture-jobs"}
CHALLENGE_RE = re.compile(
    r"자동등록방지|보안절차|사람인지 확인|로봇이 아닙니다|verify you are human|captcha|access denied|cloudflare",
    re.I,
)
OFFICIAL_ANCHOR_RE = re.compile(r"원본\s*공고\s*보기|원문\s*공고|원본\s*보기|공식\s*공고", re.I)
TITLE_DEADLINE_PATTERNS = [
    re.compile(r"(?:\(|\[|（)\s*[~～]\s*((?:20\d{2}[.\-/])?\d{1,2}[.\-/]\d{1,2})\s*\.?\s*(?:\)|\]|）)", re.I),
    re.compile(r"[~～]\s*((?:20\d{2}[.\-/])?\d{1,2}[.\-/]\d{1,2})(?:\s*(?:까지|마감))", re.I),
    re.compile(r"((?:20\d{2}[.\-/])?\d{1,2}[.\-/]\d{1,2})\s*(?:까지|마감)", re.I),
    re.compile(r"(?:마감(?:일)?|접수마감)\s*[:：]?\s*((?:20\d{2}[.\-/])?\d{1,2}[.\-/]\d{1,2})", re.I),
    re.compile(r"((?:20\d{2})?\s*\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(?:까지|마감)", re.I),
]
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


def parse_date_fragment(raw: str, ref: datetime) -> str:
    m = re.search(r"(?:(20\d{2})[.\-/]\s*)?(\d{1,2})[.\-/]\s*(\d{1,2})", raw or "")
    if not m:
        return ""
    year = int(m.group(1) or ref.year)
    month = int(m.group(2))
    day = int(m.group(3))
    try:
        d = datetime(year, month, day, tzinfo=KST)
    except ValueError:
        return ""
    if not m.group(1) and d > ref + timedelta(days=45):
        d = d.replace(year=year - 1)
    return d.strftime("%Y-%m-%d")


def title_deadline(title: str, registered: str = "") -> str:
    ref = now_kst()
    if registered:
        try:
            ref = datetime.strptime(registered, "%Y-%m-%d").replace(tzinfo=KST)
        except ValueError:
            pass
    for idx, rx in enumerate(TITLE_DEADLINE_PATTERNS):
        m = rx.search(title or "")
        if not m:
            continue
        if idx == 4:
            raw = f"{m.group(1).strip()}.{m.group(2)}"
        else:
            raw = m.group(1)
        parsed = parse_date_fragment(raw, ref)
        if parsed:
            return parsed
    return ""


def compact(value: str) -> str:
    value = re.sub(r"^\(?\s*복사\s*\)?\s*", "", value or "", flags=re.I)
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", value).lower()


def title_tokens(value: str) -> list[str]:
    tokens = [t.lower() for t in re.findall(r"[0-9A-Za-z가-힣]{2,}", value or "")]
    return [t for t in tokens if t not in GENERIC_TITLE_TOKENS]


def title_matches(expected: str, actual_text: str) -> bool:
    e = compact(expected)
    a = compact(actual_text[:16000])
    if len(e) >= 6 and e in a:
        return True
    tokens = list(dict.fromkeys(title_tokens(expected)))
    if not tokens:
        return False
    hits = sum(1 for token in tokens if token in a)
    required = 1 if len(tokens) == 1 and len(tokens[0]) >= 5 else max(2, (len(tokens) + 1) // 2)
    return hits >= required


def requested_numeric_id(row: dict, target: str) -> str:
    sid = str(row.get("sourceIdentity") or "")
    m = re.fullmatch(r"culture:id:(\d+)", sid)
    if m:
        return m.group(1)
    try:
        return str((parse_qs(urlparse(target).query).get("id") or [""])[0])
    except Exception:
        return ""


def lessoninfo_route_matches(target: str, final_url: str, expected_id: str) -> bool:
    try:
        f = urlparse(final_url)
        if (f.hostname or "").lower() not in {"lessoninfo.co.kr", "www.lessoninfo.co.kr"}:
            return False
        if f.path.lower() in LIST_PATHS or f.path.lower() != "/culture-jobs/detail.php":
            return False
        actual_id = str((parse_qs(f.query).get("id") or [""])[0])
        return bool(expected_id) and actual_id == expected_id
    except Exception:
        return False


def candidate_official_links(html: str, base_url: str, existing: list[str] | None = None) -> list[str]:
    out: list[str] = []
    for raw in existing or []:
        if raw:
            out.append(str(raw))
    soup = BeautifulSoup(html or "", "html.parser")
    for a in soup.select("a[href]"):
        label = " ".join(a.stripped_strings)
        if not OFFICIAL_ANCHOR_RE.search(label):
            continue
        href = urljoin(base_url, str(a.get("href") or ""))
        p = urlparse(href)
        if p.scheme not in {"http", "https"}:
            continue
        if (p.hostname or "").lower() in {"lessoninfo.co.kr", "www.lessoninfo.co.kr"}:
            continue
        out.append(href)
    return list(dict.fromkeys(out))[:5]


async def body_text(page) -> str:
    try:
        return await page.locator("body").inner_text(timeout=4000)
    except Exception:
        return ""


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


async def verify_official(browser, url: str, expected_title: str) -> tuple[bool, str, str]:
    context = await cold_context(browser)
    page = await context.new_page()
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        try:
            await page.wait_for_load_state("networkidle", timeout=2500)
        except PlaywrightTimeoutError:
            pass
        text = await body_text(page)
        if CHALLENGE_RE.search(text[:8000]):
            return False, "official-challenge", page.url
        if response is not None and response.status >= 400:
            return False, f"official-http-{response.status}", page.url
        final = urlparse(page.url)
        if final.scheme not in {"http", "https"} or not final.hostname:
            return False, "official-invalid-final-url", page.url
        if final.path in {"", "/"} and not final.query:
            return False, "official-homepage-only", page.url
        if not title_matches(expected_title, text):
            return False, "official-title-mismatch", page.url
        return True, "official-title-match", page.url
    except Exception as exc:
        return False, f"official-error:{type(exc).__name__}", ""
    finally:
        await page.close()
        await context.close()


async def verify_culture_row(browser, sem: asyncio.Semaphore, row: dict) -> dict:
    async with sem:
        result = dict(row)
        now = now_kst().isoformat(timespec="seconds")
        target = str(
            row.get("unverifiedDetailUrl")
            or row.get("detailUrl")
            or row.get("originalUrl")
            or row.get("url")
            or ""
        )
        result["verifiedAt"] = now
        result["detailUrl"] = target
        result["unverifiedDetailUrl"] = target
        sid = str(row.get("sourceIdentity") or "")

        if sid in KNOWN_BAD_COLD_IDS:
            result.update({
                "detailLinkVerified": False,
                "detailLinkReason": "known-bad-cold-regression-sentinel",
                "detailLinkVerificationReason": "known-bad-cold-regression-sentinel",
                "verifiedUrl": "",
                "resolvedUrlType": "",
                "url": "",
                "originalUrl": "",
            })
            return result

        expected_id = requested_numeric_id(row, target)
        if not target or not expected_id:
            reason = "missing-detail-url-or-id"
            result.update({
                "detailLinkVerified": False,
                "detailLinkReason": reason,
                "detailLinkVerificationReason": reason,
                "verifiedUrl": "",
                "resolvedUrlType": "",
                "url": "",
                "originalUrl": "",
            })
            return result

        context = await cold_context(browser)
        page = await context.new_page()
        try:
            response = await page.goto(target, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            try:
                await page.wait_for_load_state("networkidle", timeout=2500)
            except PlaywrightTimeoutError:
                pass
            text = await body_text(page)
            html = await page.content()
            final_url = page.url
            if CHALLENGE_RE.search(text[:8000]):
                reason = "cold-browser-challenge"
            elif response is not None and response.status >= 400:
                reason = f"cold-browser-http-{response.status}"
            elif not lessoninfo_route_matches(target, final_url, expected_id):
                reason = "cold-browser-route-mismatch"
            elif not title_matches(str(row.get("title") or ""), text):
                reason = "cold-browser-title-mismatch"
            else:
                official_candidates = candidate_official_links(
                    html, final_url, list(row.get("linkedOfficialUrls") or [])
                )
                for official in official_candidates:
                    ok, _, official_final = await verify_official(
                        browser, official, str(row.get("title") or "")
                    )
                    if ok:
                        linked = list(dict.fromkeys([
                            *(row.get("linkedOfficialUrls") or []), official_final
                        ]))
                        result.update({
                            "detailLinkVerified": True,
                            "detailLinkReason": "cold-browser-official-title-match",
                            "detailLinkVerificationReason": "cold-browser-official-title-match",
                            "verifiedUrl": official_final,
                            "resolvedUrlType": "official",
                            "lessoninfoVerifiedUrl": final_url,
                            "url": official_final,
                            "originalUrl": official_final,
                            "linkedOfficialUrls": linked,
                        })
                        return result
                result.update({
                    "detailLinkVerified": True,
                    "detailLinkReason": "cold-browser-id-title-match",
                    "detailLinkVerificationReason": "cold-browser-id-title-match",
                    "verifiedUrl": final_url,
                    "resolvedUrlType": "lessoninfo",
                    "lessoninfoVerifiedUrl": final_url,
                    "url": final_url,
                    "originalUrl": final_url,
                })
                return result
        except Exception as exc:
            reason = f"cold-browser-error:{type(exc).__name__}"
        finally:
            await page.close()
            await context.close()

        result.update({
            "detailLinkVerified": False,
            "detailLinkReason": reason,
            "detailLinkVerificationReason": reason,
            "verifiedUrl": "",
            "resolvedUrlType": "",
            "url": "",
            "originalUrl": "",
        })
        return result


async def run() -> int:
    payload = load_json(DATA, {"jobs": []})
    jobs = payload if isinstance(payload, list) else list(payload.get("jobs") or [])
    today = now_kst().date()
    expired_removed = []
    retained: list[dict] = []
    culture_to_verify: list[dict] = []

    for row in jobs:
        if row.get("sourceSurface") != "culture-arts":
            retained.append(row)
            continue
        updated = dict(row)
        deadline = title_deadline(str(row.get("title") or ""), str(row.get("registered") or ""))
        if deadline:
            updated["applyEnd"] = deadline
            updated["activeReason"] = "explicit-title-deadline"
        end = str(updated.get("applyEnd") or "")
        if end:
            try:
                if datetime.strptime(end, "%Y-%m-%d").date() < today:
                    expired_removed.append({
                        "sourceIdentity": updated.get("sourceIdentity"),
                        "title": updated.get("title"),
                        "applyEnd": end,
                    })
                    continue
            except ValueError:
                pass
        culture_to_verify.append(updated)

    started = now_kst().isoformat(timespec="seconds")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        sem = asyncio.Semaphore(CONCURRENCY)
        verified_rows = await asyncio.gather(
            *(verify_culture_row(browser, sem, row) for row in culture_to_verify)
        )
        await browser.close()

    retained.extend(verified_rows)
    retained.sort(
        key=lambda j: (
            str(j.get("registered") or ""),
            str(j.get("applyEnd") or "9999-12-31"),
            str(j.get("sourceIdentity") or ""),
        ),
        reverse=True,
    )

    if isinstance(payload, list):
        output = retained
    else:
        output = dict(payload)
        output["updatedAt"] = now_kst().strftime("%Y-%m-%d %H:%M KST")
        output["policy"] = "active-only-browser-v1+cold-link-v2"
        output["jobs"] = retained
    write_json(DATA, output)

    culture_rows = [j for j in retained if j.get("sourceSurface") == "culture-arts"]
    verified = [j for j in culture_rows if j.get("detailLinkVerified") is True]
    unverified = [j for j in culture_rows if j.get("detailLinkVerified") is False]
    missing = [j for j in culture_rows if j.get("detailLinkVerified") not in {True, False}]
    official = [j for j in verified if j.get("resolvedUrlType") == "official"]
    lessoninfo = [j for j in verified if j.get("resolvedUrlType") == "lessoninfo"]
    reasons: dict[str, int] = {}
    for row in culture_rows:
        reason = str(row.get("detailLinkReason") or "missing")
        reasons[reason] = reasons.get(reason, 0) + 1

    report = {
        "generatedAt": now_kst().isoformat(timespec="seconds"),
        "startedAt": started,
        "policy": "lessoninfo-culture-cold-link-v2",
        "healthy": len(missing) == 0,
        "totalInputJobs": len(jobs),
        "expiredCultureRemoved": len(expired_removed),
        "expiredCultureExamples": expired_removed[:20],
        "eligibleCultureRows": len(culture_rows),
        "verifiedCultureRows": len(verified),
        "verifiedOfficialRows": len(official),
        "verifiedLessoninfoRows": len(lessoninfo),
        "unverifiedCultureRows": len(unverified),
        "verificationMissingCount": len(missing),
        "verificationMissingExamples": [j.get("sourceIdentity") for j in missing[:20]],
        "knownBadSentinels": sorted(KNOWN_BAD_COLD_IDS),
        "reasonCounts": reasons,
    }
    write_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["healthy"] else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
