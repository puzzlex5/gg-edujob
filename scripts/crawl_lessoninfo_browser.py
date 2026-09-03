#!/usr/bin/env python3
"""Browser-rendered Lessoninfo collector for current recruitment postings only.

Targets the exact public pages requested by the user:
- 방과후교사•늘봄학교 강사
- 문화예술 채용

This does not solve or bypass CAPTCHAs. If Lessoninfo serves a human-verification page to a
normal Chromium session, the run fails closed and preserves the previous known-good dataset.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "lessoninfo_jobs.json"
LEDGER = ROOT / "lessoninfo_source_id_ledger.json"
REPORT = ROOT / "lessoninfo_reconciliation_report.json"
STATE = ROOT / "lessoninfo_collection_state.json"
KST_OFFSET = timedelta(hours=9)
MAX_PAGES = int(os.getenv("LESSONINFO_BROWSER_MAX_PAGES", "35"))
DETAIL_CONCURRENCY = int(os.getenv("LESSONINFO_BROWSER_DETAIL_CONCURRENCY", "4"))
DISCOVERY_DAYS = int(os.getenv("LESSONINFO_BROWSER_DISCOVERY_DAYS", "120"))
NO_DEADLINE_FRESH_DAYS = int(os.getenv("LESSONINFO_NO_DEADLINE_FRESH_DAYS", "30"))

TARGETS = [
    {
        "key": "afterschool-nulbom",
        "label": "방과후교사•늘봄학교 강사",
        "listUrl": "https://www.lessoninfo.co.kr/board/board.php?board_code=20130529164321_7227&code=&bo_table=20170316171033_6494",
        "boTable": "20170316171033_6494",
    },
    {
        "key": "culture-arts",
        "label": "문화예술 채용",
        "listUrl": "https://www.lessoninfo.co.kr/culture-jobs/list.php",
    },
]

CHALLENGE_RE = re.compile(
    r"자동등록방지|보안절차|사람인지 확인|로봇이 아닙니다|prove that you are human|verify you are human|captcha|access denied|cloudflare",
    re.I,
)
CLOSED_RE = re.compile(
    r"\b구인\s*완료\b|\(\s*완료\s*\)|\[\s*완료\s*\]|채용\s*완료|모집\s*완료|"
    r"마감되었습니다|모집이\s*마감|채용이\s*마감|접수가\s*마감|종료되었습니다|공고\s*종료",
    re.I,
)
ALWAYS_OPEN_RE = re.compile(r"상시\s*채용|상시\s*모집|채용\s*시까지|충원\s*시까지|채용\s*완료\s*시까지", re.I)
RECRUIT_RE = re.compile(r"구인|채용|모집|강사|교사|선생님|지도자|단원|연주자|지휘|반주|방과후|늘봄", re.I)
EXCLUDE_RE = re.compile(
    r"구직|이력서|취업희망|매매|팝니다|판매|삽니다|양도|인수|권리금|임대|대여|연습실|"
    r"악기매매|중고악기|원생모집|학생모집|레슨생|홍보|콩쿠르|콩쿨|공연정보|세무\s*상담|사기꾼",
    re.I,
)
DATE_RE = re.compile(r"(?:(20\d{2})[.\-/년]\s*)?(\d{1,2})[.\-/월]\s*(\d{1,2})")
DEADLINE_PATTERNS = [
    re.compile(
        r"(?:마감일자|마감일|접수마감(?:일자|일)?|모집마감(?:일자|일)?|채용마감(?:일자|일)?|원서접수마감)\s*[:：|]?\s*"
        r"((?:20\d{2})[.\-/년]\s*\d{1,2}[.\-/월]\s*\d{1,2})",
        re.I,
    ),
    re.compile(
        r"(?:접수기간|지원기간|모집기간|원서접수)\s*[:：|]?[^~～\n]{0,100}[~～]\s*"
        r"((?:20\d{2})?[.\-/년]?\s*\d{1,2}[.\-/월]\s*\d{1,2})",
        re.I,
    ),
]


def now_kst() -> datetime:
    from datetime import timezone
    return datetime.now(timezone(KST_OFFSET))


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_date(text: str, ref: datetime | None = None) -> str:
    m = DATE_RE.search(text or "")
    if not m:
        return ""
    ref = ref or now_kst()
    year = int(m.group(1) or ref.year)
    month, day = int(m.group(2)), int(m.group(3))
    try:
        dt = datetime(year, month, day, tzinfo=ref.tzinfo)
        # For month/day-only records around New Year, avoid accidentally assigning a far-future year.
        if not m.group(1) and dt > ref + timedelta(days=45):
            dt = dt.replace(year=year - 1)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return ""


def parse_deadline(text: str, registered: str = "") -> tuple[str, str]:
    if ALWAYS_OPEN_RE.search(text or ""):
        return "", "always-open"
    ref = now_kst()
    if registered:
        try:
            ref = datetime.strptime(registered, "%Y-%m-%d").replace(tzinfo=ref.tzinfo)
        except ValueError:
            pass
    for rx in DEADLINE_PATTERNS:
        m = rx.search(text or "")
        if m:
            d = parse_date(m.group(1), ref)
            if d:
                return d, "explicit-deadline"
    return "", ""


def add_page_param(url: str, page: int) -> str:
    p = urlparse(url)
    q = parse_qs(p.query, keep_blank_values=True)
    q["page"] = [str(page)]
    query = urlencode([(k, v) for k, vals in q.items() for v in vals])
    return urlunparse((p.scheme, p.netloc, p.path, p.params, query, p.fragment))


def canonical_url(url: str) -> str:
    p = urlparse(url)
    q = parse_qs(p.query, keep_blank_values=True)
    # remove common tracking/session-like keys; retain content identity/query keys
    for k in list(q):
        if k.lower().startswith("utm_") or k.lower() in {"ckattempt", "ref", "from"}:
            q.pop(k, None)
    query = urlencode(sorted((k, v) for k, vals in q.items() for v in vals))
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, "", query, ""))


def stable_id(url: str, target: dict) -> str:
    p = urlparse(url)
    q = parse_qs(p.query)
    if target["key"] == "afterschool-nulbom":
        wr = (q.get("wr_no") or [""])[0]
        bt = (q.get("bo_table") or [target.get("boTable", "")])[0]
        if wr.isdigit() and bt:
            return f"board:{bt}:{wr}"
    for key in ("id", "idx", "no", "seq", "job_no", "jobNo", "wr_no"):
        value = (q.get(key) or [""])[0]
        if value and re.fullmatch(r"[A-Za-z0-9_-]+", value):
            return f"culture:{key}:{value}"
    # Exact canonical detail URL is strong identity when the new culture-jobs route has no exposed numeric key.
    canon = canonical_url(url)
    return "culture:url:" + hashlib.sha1(canon.encode("utf-8")).hexdigest()[:20]


def candidate_from_anchor(href: str, text: str, row_text: str, target: dict) -> dict | None:
    if not href:
        return None
    u = urljoin(target["listUrl"], href)
    p = urlparse(u)
    if p.netloc.lower() not in {"www.lessoninfo.co.kr", "lessoninfo.co.kr"}:
        return None
    title = re.sub(r"\s+", " ", text or "").strip()
    row = re.sub(r"\s+", " ", row_text or title).strip()
    if target["key"] == "afterschool-nulbom":
        q = parse_qs(p.query)
        bt = (q.get("bo_table") or [""])[0]
        wr = (q.get("wr_no") or [""])[0]
        if bt != target["boTable"] or not wr.isdigit():
            return None
    else:
        path = p.path.lower()
        if not path.startswith("/culture-jobs/") or path.endswith("/list.php"):
            return None
        if any(x in path for x in ("write", "login", "search", "category")):
            return None
        # Navigation links usually have no recruitment-like text; require either a recruitment signal
        # or a detail-looking identifier/path.
        q = parse_qs(p.query)
        has_id = any((q.get(k) or [""])[0] for k in ("id", "idx", "no", "seq", "job_no", "jobNo", "wr_no"))
        detail_path = any(x in path for x in ("view", "detail", "read"))
        if not (has_id or detail_path or RECRUIT_RE.search(title + " " + row)):
            return None
    sid = stable_id(u, target)
    return {
        "sourceIdentity": sid,
        "url": canonical_url(u),
        "titleHint": title,
        "rowText": row,
        "registeredHint": parse_date(row),
        "sourceSurface": target["key"],
        "sourceSurfaceLabel": target["label"],
    }


async def visible_text(page) -> str:
    try:
        return await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


async def load(page, url: str) -> tuple[str, str]:
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        await page.wait_for_load_state("networkidle", timeout=7000)
    except PlaywrightTimeoutError:
        pass
    await page.wait_for_timeout(900)
    text = await visible_text(page)
    html = await page.content()
    if CHALLENGE_RE.search(text[:10000]):
        raise RuntimeError("human_verification_challenge")
    return html, text


async def extract_candidates(page, target: dict) -> list[dict]:
    raw = await page.locator("a[href]").evaluate_all(
        """els => els.map(a => ({
          href: a.href || a.getAttribute('href') || '',
          text: (a.innerText || a.textContent || '').trim(),
          row: ((a.closest('tr, li, article, section, .list, .item, .row') || a.parentElement)?.innerText || '').trim()
        }))"""
    )
    found = {}
    for a in raw:
        item = candidate_from_anchor(a.get("href", ""), a.get("text", ""), a.get("row", ""), target)
        if item:
            found.setdefault(item["sourceIdentity"], item)
    return list(found.values())


async def crawl_list(context, target: dict) -> tuple[list[dict], dict]:
    page = await context.new_page()
    found: dict[str, dict] = {}
    fingerprints = set()
    pages = 0
    old_streak = 0
    cutoff = now_kst() - timedelta(days=DISCOVERY_DAYS)
    stop = "max_pages"
    complete = False
    try:
        for n in range(1, MAX_PAGES + 1):
            url = target["listUrl"] if n == 1 else add_page_param(target["listUrl"], n)
            await load(page, url)
            pages += 1
            rows = await extract_candidates(page, target)
            ids = tuple(sorted(x["sourceIdentity"] for x in rows))
            if not rows:
                stop = "end_of_pages" if found and n > 1 else "unexpected_empty_first_page"
                complete = bool(found and n > 1)
                break
            if ids in fingerprints:
                stop = "repeated_page_boundary"
                complete = bool(found)
                break
            fingerprints.add(ids)
            before = len(found)
            for item in rows:
                found.setdefault(item["sourceIdentity"], item)
            if len(found) == before:
                stop = "no_new_ids"
                complete = True
                break

            dated = []
            for x in rows:
                if x.get("registeredHint"):
                    try:
                        dated.append(datetime.strptime(x["registeredHint"], "%Y-%m-%d").replace(tzinfo=now_kst().tzinfo))
                    except ValueError:
                        pass
            if dated and all(d < cutoff for d in dated):
                old_streak += 1
                if old_streak >= 2:
                    stop = "discovery_horizon_crossed"
                    complete = True
                    break
            else:
                old_streak = 0
        return list(found.values()), {
            "surface": target["label"],
            "listUrl": target["listUrl"],
            "complete": complete,
            "pagesScanned": pages,
            "ids": len(found),
            "stopReason": stop,
        }
    finally:
        await page.close()


def classify_detail(html: str, text: str, item: dict) -> tuple[dict | None, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = item.get("titleHint", "")
    for selector in ("h1", "h2", "h3", ".subject", ".title", "meta[property='og:title']", "title"):
        el = soup.select_one(selector)
        if el:
            t = el.get("content", "") if el.name == "meta" else " ".join(el.stripped_strings)
            t = re.sub(r"\s+", " ", t or "").strip()
            if len(t) >= 2:
                title = t
                break
    signal = (title + " " + text[:18000]).strip()
    if CLOSED_RE.search(title) or CLOSED_RE.search(text[:7000]):
        return None, "closed-marker"
    if EXCLUDE_RE.search(title) and not RECRUIT_RE.search(title):
        return None, "non-recruitment"
    if not RECRUIT_RE.search(signal):
        return None, "non-recruitment"

    registered = item.get("registeredHint") or parse_date(text)
    deadline, deadline_reason = parse_deadline(text, registered)
    today = now_kst().date()
    if deadline:
        try:
            if datetime.strptime(deadline, "%Y-%m-%d").date() < today:
                return None, "expired-deadline"
        except ValueError:
            pass
    elif not ALWAYS_OPEN_RE.search(signal):
        if not registered:
            return None, "no-date-no-deadline"
        try:
            reg = datetime.strptime(registered, "%Y-%m-%d").date()
            if reg < today - timedelta(days=NO_DEADLINE_FRESH_DAYS):
                return None, "stale-without-deadline"
        except ValueError:
            return None, "invalid-date-no-deadline"

    # Capture a useful location hint without dropping other regions. '전부' means the collection
    # should preserve every active posting from the two requested Lessoninfo categories.
    location = ""
    for rx in (
        re.compile(r"(?:지역|근무지역|소재지|기관주소|주소)\s*[:：|]?\s*([^|\n]{2,60})"),
        re.compile(r"(서울(?:특별시)?\s*\S{0,15}|경기(?:도)?\s*\S{0,15}|인천(?:광역시)?\s*\S{0,15})"),
    ):
        m = rx.search(text)
        if m:
            location = re.sub(r"\s+", " ", m.group(1)).strip()[:80]
            break

    return {
        "sourceIdentity": item["sourceIdentity"],
        "source": "레슨인포",
        "sourceType": "민간 구인",
        "trustLevel": "민간출처",
        "category": "private-recruitment",
        "sourceSurface": item["sourceSurface"],
        "sourceSurfaceLabel": item["sourceSurfaceLabel"],
        "title": title[:300],
        "registered": registered,
        "applyEnd": deadline,
        "activeReason": deadline_reason or ("always-open" if ALWAYS_OPEN_RE.search(signal) else "fresh-no-deadline"),
        "location": location,
        "url": item["url"],
        "originalUrl": item["url"],
        "collectedAt": now_kst().isoformat(timespec="seconds"),
    }, "active"


async def fetch_detail(context, sem: asyncio.Semaphore, item: dict) -> tuple[str, dict | None, str, str]:
    async with sem:
        page = await context.new_page()
        try:
            html, text = await load(page, item["url"])
            job, reason = classify_detail(html, text, item)
            return item["sourceIdentity"], job, reason, ""
        except Exception as e:
            return item["sourceIdentity"], None, "detail-error", f"{type(e).__name__}:{e}"
        finally:
            await page.close()


async def run() -> int:
    previous = read_json(OUT, {"jobs": []})
    prev_jobs = previous if isinstance(previous, list) else previous.get("jobs", [])
    prev_by_id = {j.get("sourceIdentity"): j for j in prev_jobs if j.get("sourceIdentity")}
    source_reports = []
    candidates: dict[str, dict] = {}
    errors = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        context = await browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1440, "height": 1100},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        )
        await context.set_extra_http_headers({"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5"})

        complete = True
        for target in TARGETS:
            try:
                rows, report = await crawl_list(context, target)
                source_reports.append(report)
                complete &= bool(report.get("complete"))
                for item in rows:
                    # Cross-surface exact URL duplicates are kept under the first stable occurrence only.
                    candidates.setdefault(item["sourceIdentity"], item)
            except Exception as e:
                complete = False
                errors.append(f"{target['key']}:{type(e).__name__}:{e}")
                source_reports.append({
                    "surface": target["label"], "listUrl": target["listUrl"],
                    "complete": False, "ids": 0, "error": f"{type(e).__name__}:{e}",
                })

        active: dict[str, dict] = {}
        classifications: dict[str, str] = {}
        detail_errors = []
        if candidates:
            sem = asyncio.Semaphore(max(1, DETAIL_CONCURRENCY))
            results = await asyncio.gather(*(fetch_detail(context, sem, item) for item in candidates.values()))
            for sid, job, reason, error in results:
                classifications[sid] = reason
                if error:
                    detail_errors.append({"id": sid, "error": error})
                    if sid in prev_by_id:
                        active[sid] = prev_by_id[sid]
                        classifications[sid] = "retained-on-detail-error"
                elif job:
                    active[sid] = job

        await context.close()
        await browser.close()

    # A source returning zero discovered IDs is never interpreted as 'no jobs'.
    source_id_ok = len(source_reports) == len(TARGETS) and all((r.get("ids") or 0) > 0 for r in source_reports)
    healthy = complete and source_id_ok and not errors and not detail_errors
    now = now_kst().isoformat(timespec="seconds")

    if healthy:
        jobs = sorted(
            active.values(),
            key=lambda j: (j.get("registered") or "", j.get("applyEnd") or "9999-12-31", j.get("sourceIdentity") or ""),
            reverse=True,
        )
        write_json(OUT, {
            "updatedAt": now_kst().strftime("%Y-%m-%d %H:%M KST"),
            "source": "레슨인포",
            "category": "private-recruitment",
            "policy": "active-only-browser-v1",
            "jobs": jobs,
        })
    else:
        jobs = prev_jobs

    published_ids = {j.get("sourceIdentity") for j in jobs if j.get("sourceIdentity")}
    active_ids = {sid for sid, reason in classifications.items() if reason in {"active", "retained-on-detail-error"}}
    missing_after = sorted(active_ids - published_ids) if healthy else []

    ledger = read_json(LEDGER, {"ids": {}, "snapshots": []})
    ledger.setdefault("ids", {})
    for sid, item in candidates.items():
        ent = ledger["ids"].setdefault(sid, {"firstSeenAt": now})
        ent.update({
            "lastSeenAt": now,
            "url": item["url"],
            "surface": item["sourceSurface"],
            "classification": classifications.get(sid, "unclassified"),
        })
    per_surface_active = {t["key"]: 0 for t in TARGETS}
    for job in (active.values() if healthy else []):
        key = job.get("sourceSurface")
        if key in per_surface_active:
            per_surface_active[key] += 1
    ledger.setdefault("snapshots", []).append({
        "at": now, "candidateIds": len(candidates), "activeIds": len(active) if healthy else None,
        "perSurfaceActive": per_surface_active if healthy else None, "healthy": healthy,
    })
    ledger["snapshots"] = ledger["snapshots"][-180:]
    write_json(LEDGER, ledger)

    counts = {}
    for reason in classifications.values():
        counts[reason] = counts.get(reason, 0) + 1
    report = {
        "generatedAt": now,
        "policy": "lessoninfo-active-browser-v1",
        "healthy": healthy,
        "traversalComplete": complete,
        "sourceReports": source_reports,
        "candidateIdCount": len(candidates),
        "activeIdCount": len(active) if healthy else 0,
        "publishedIdCount": len(published_ids),
        "perSurfaceActive": per_surface_active,
        "missingAfterCount": len(missing_after),
        "missingAfterExamples": missing_after[:20],
        "detailErrorCount": len(detail_errors),
        "detailErrorExamples": detail_errors[:20],
        "classificationCounts": counts,
        "preservedPreviousDataset": not healthy,
        "errors": errors,
    }
    write_json(REPORT, report)
    state = read_json(STATE, {})
    write_json(STATE, {
        "updatedAt": now,
        "healthy": healthy,
        "lastGoodAt": now if healthy else state.get("lastGoodAt"),
        "activeIdCount": len(active) if healthy else state.get("activeIdCount", 0),
    })
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if healthy and not missing_after else 3


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
