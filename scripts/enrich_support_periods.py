#!/usr/bin/env python3
"""Enrich support-office jobs with application/work periods from exact detail pages.

Support-office list boards often expose only title + registration date. This pass
opens the exact individual announcement (GET for Gyeonggi MirCMS, POST job_seq
for Seoul JOV11), extracts labelled date ranges, caches results, and prevents the
frontend from rendering meaningless '- ~ -' date rows.
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
JOBS_PATH = ROOT / "jobs.json"
CACHE_PATH = ROOT / "support_period_cache.json"
INDEX_PATH = ROOT / "index.html"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
UA = "Mozilla/5.0 (compatible; metro-edujob-period-enricher/1.1)"
PARSER_VERSION = 2
# Full Korean/numeric dates: 2026.08.20 / 2026-8-20 / 2026년 8월 20일.
FULL_DATE_RE = re.compile(r"(20\d{2})\s*(?:[./-]\s*|년\s*)(\d{1,2})\s*(?:[./-]\s*|월\s*)(\d{1,2})(?:\s*일)?")
# Common abbreviated range tail: 2026. 8. 20. ~ 8. 25. (inherit the preceding year).
PARTIAL_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[./-]\s*(\d{1,2})(?:\s*일)?")
SOFT_404 = re.compile(r"페이지를\s*찾을\s*수\s*없|존재하지\s*않는\s*페이지|잘못된\s*접근|요청하신\s*페이지가\s*없", re.I)
APPLY_LABEL = re.compile(r"접수\s*기간|원서\s*접수|서류\s*접수|응시원서\s*접수|공고\s*및\s*접수|접수\s*일시|접수\s*일정|접수\s*마감|지원서\s*접수", re.I)
WORK_LABEL = re.compile(r"채용\s*기간|계약\s*기간|근무\s*기간|임용\s*기간|근로\s*기간|채용\s*예정\s*기간|근로계약\s*기간|임용\s*예정\s*기간", re.I)
_thread = threading.local()


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def date_norm(value: str) -> str:
    m = FULL_DATE_RE.search(value or "")
    if not m:
        return ""
    return f"{int(m.group(1)):04d}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"


def date_obj(value: str):
    try:
        return datetime.strptime(value, "%Y/%m/%d").replace(tzinfo=KST)
    except Exception:
        return None


def recent_job(job, days=75):
    d = date_obj(job.get("registered", ""))
    return d is None or d >= NOW - timedelta(days=days)


def session():
    if not hasattr(_thread, "session"):
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5"})
        _thread.session = s
    return _thread.session


def all_dates(text: str):
    """Return dates in order, including an abbreviated second date that inherits the year.

    Korean school notices frequently write ranges as `2026. 8. 20.(목) ~ 8. 25.(화)`.
    The previous parser only saw the first date, causing avoidable blank end dates.
    """
    text = text or ""
    found = []
    spans = []
    last_year = None
    for m in FULL_DATE_RE.finditer(text):
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            found.append((m.start(), f"{year:04d}/{month:02d}/{day:02d}"))
            spans.append((m.start(), m.end()))
            last_year = year
    if last_year is not None:
        for m in PARTIAL_DATE_RE.finditer(text):
            if any(a <= m.start() < b or a < m.end() <= b for a, b in spans):
                continue
            month, day = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12 and 1 <= day <= 31:
                found.append((m.start(), f"{last_year:04d}/{month:02d}/{day:02d}"))
    found.sort(key=lambda x: x[0])
    return list(dict.fromkeys(value for _, value in found))


def period_from_chunk(chunk: str, label_re: re.Pattern):
    if not label_re.search(chunk or ""):
        return "", ""
    m = label_re.search(chunk)
    tail = chunk[m.start():m.start() + 340]
    dates = all_dates(tail)
    if len(dates) >= 2:
        return dates[0], dates[1]
    if len(dates) == 1:
        # One-sided dates are still useful; use it as an end date when the wording
        # explicitly says deadline/until, otherwise as the start date.
        if re.search(r"마감|까지|접수종료", tail):
            return "", dates[0]
        return dates[0], ""
    return "", ""


def labelled_chunks(soup: BeautifulSoup):
    chunks = []
    for tr in soup.find_all("tr"):
        text = clean(tr.get_text(" ", strip=True))
        if text:
            chunks.append(text)
    for dl in soup.find_all("dl"):
        text = clean(dl.get_text(" ", strip=True))
        if text:
            chunks.append(text)
    for el in soup.find_all(["p", "li", "div"]):
        text = clean(el.get_text(" ", strip=True))
        if text and len(text) <= 650 and (APPLY_LABEL.search(text) or WORK_LABEL.search(text)):
            chunks.append(text)
    return list(dict.fromkeys(chunks))


def extract_periods(html: str):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    full = clean(soup.get_text(" ", strip=True))
    if SOFT_404.search(full):
        return {"status": "soft404", "applyStart": "", "applyEnd": "", "workStart": "", "workEnd": ""}

    apply_start = apply_end = work_start = work_end = ""
    chunks = labelled_chunks(soup)
    for chunk in chunks:
        if not (apply_start or apply_end) and APPLY_LABEL.search(chunk):
            apply_start, apply_end = period_from_chunk(chunk, APPLY_LABEL)
        if not (work_start or work_end) and WORK_LABEL.search(chunk):
            work_start, work_end = period_from_chunk(chunk, WORK_LABEL)
        if (apply_start or apply_end) and (work_start or work_end):
            break

    if not (apply_start or apply_end):
        apply_start, apply_end = period_from_chunk(full, APPLY_LABEL)
    if not (work_start or work_end):
        work_start, work_end = period_from_chunk(full, WORK_LABEL)

    return {
        "status": "parsed" if any((apply_start, apply_end, work_start, work_end)) else "unavailable",
        "applyStart": apply_start,
        "applyEnd": apply_end,
        "workStart": work_start,
        "workEnd": work_end,
    }


def fetch_key(job):
    if job.get("openMethod") == "POST" and job.get("openUrl") and (job.get("openParams") or {}).get("job_seq"):
        seq = str(job["openParams"]["job_seq"])
        return f"POST|{job['openUrl']}|job_seq={seq}"
    if job.get("url"):
        return "GET|" + str(job["url"])
    return ""


def fetch_one(job):
    key = fetch_key(job)
    if not key:
        return key, {"status": "no-detail-link", "applyStart": "", "applyEnd": "", "workStart": "", "workEnd": ""}
    s = session()
    try:
        if key.startswith("POST|"):
            seq = str(job["openParams"]["job_seq"])
            headers = {"Referer": job.get("boardUrl", "")} if job.get("boardUrl") else {}
            r = s.post(job["openUrl"], data={"job_seq": seq}, headers=headers, timeout=18, allow_redirects=True)
        else:
            r = s.get(job["url"], headers={"Referer": job.get("boardUrl", "")} if job.get("boardUrl") else {}, timeout=18, allow_redirects=True)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        result = extract_periods(r.text)
        result["http"] = r.status_code
        result["checkedAt"] = NOW.strftime("%Y-%m-%d %H:%M KST")
        result["parserVersion"] = PARSER_VERSION
        return key, result
    except Exception as exc:
        return key, {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:120]}", "applyStart": "", "applyEnd": "", "workStart": "", "workEnd": "", "checkedAt": NOW.strftime("%Y-%m-%d %H:%M KST"), "parserVersion": PARSER_VERSION}


def cache_fresh(item):
    if not item or item.get("parserVersion") != PARSER_VERSION:
        return False
    raw = item.get("checkedAt", "").replace(" KST", "")
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    except Exception:
        return False
    # Parsed pages are periodically re-read because schools sometimes amend dates after posting.
    if item.get("status") == "parsed":
        return dt >= NOW - timedelta(days=7)
    return dt >= NOW - timedelta(hours=20)


def patch_frontend():
    text = INDEX_PATH.read_text(encoding="utf-8")
    changed = False
    marker = "function periodText(start,end){"
    if marker not in text:
        needle = "function fmt(s){const d=parseDate(s);if(!d)return s||'-';return `${d.getFullYear()}.${String(d.getMonth()+1).padStart(2,'0')}.${String(d.getDate()).padStart(2,'0')}`}\n"
        addition = needle + "function periodText(start,end){if(start&&end)return `${fmt(start)} ~ ${fmt(end)}`;if(end)return `~ ${fmt(end)}`;if(start)return `${fmt(start)} ~`;return '원문 확인'}\n"
        if needle in text:
            text = text.replace(needle, addition, 1)
            changed = True
    old = "<div><b>접수</b> ${fmt(j.applyStart)} ~ ${fmt(j.applyEnd)}</div><div><b>채용</b> ${fmt(j.workStart)} ~ ${fmt(j.workEnd)}</div>"
    new = "<div><b>접수</b> ${periodText(j.applyStart,j.applyEnd)}</div><div><b>채용</b> ${periodText(j.workStart,j.workEnd)}</div>"
    if old in text:
        text = text.replace(old, new, 1)
        changed = True
    if changed:
        INDEX_PATH.write_text(text, encoding="utf-8")
    return changed


def main():
    payload = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    jobs = payload.get("jobs", [])
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    except Exception:
        cache = {}

    candidates = []
    keys_to_jobs = {}
    for job in jobs:
        if job.get("sourceType") != "교육지원청 개별 게시판":
            continue
        if not recent_job(job):
            continue
        if all(job.get(k) for k in ("applyStart", "applyEnd", "workStart", "workEnd")):
            continue
        key = fetch_key(job)
        if not key:
            job["periodStatus"] = "no-detail-link"
            continue
        keys_to_jobs.setdefault(key, []).append(job)
        if not cache_fresh(cache.get(key)):
            candidates.append(job)

    unique = {}
    for job in candidates:
        unique.setdefault(fetch_key(job), job)
    to_fetch = list(unique.values())[:700]
    cached_count = sum(1 for key in keys_to_jobs if cache_fresh(cache.get(key)))
    pending_count = max(0, len(keys_to_jobs) - cached_count - len(to_fetch))
    print(f"PERIOD ENRICH candidates={len(keys_to_jobs)} fetch={len(to_fetch)} cached={cached_count} pending={pending_count}")

    if to_fetch:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(fetch_one, job) for job in to_fetch]
            for fut in as_completed(futures):
                key, result = fut.result()
                if key:
                    cache[key] = result

    filled_fields = 0
    parsed_jobs = 0
    unavailable_jobs = 0
    for key, grouped in keys_to_jobs.items():
        result = cache.get(key, {})
        for job in grouped:
            before = sum(bool(job.get(k)) for k in ("applyStart", "applyEnd", "workStart", "workEnd"))
            for field in ("applyStart", "applyEnd", "workStart", "workEnd"):
                if not job.get(field) and result.get(field):
                    job[field] = result[field]
            after = sum(bool(job.get(k)) for k in ("applyStart", "applyEnd", "workStart", "workEnd"))
            filled_fields += max(0, after - before)
            job["periodStatus"] = result.get("status", "pending" if key in {fetch_key(x) for x in to_fetch} else "unavailable")
            if result.get("status") == "parsed":
                parsed_jobs += 1
            elif not any(job.get(k) for k in ("applyStart", "applyEnd", "workStart", "workEnd")):
                unavailable_jobs += 1

    support_recent = [j for j in jobs if j.get("sourceType") == "교육지원청 개별 게시판" and recent_job(j)]
    missing_apply = sum(1 for j in support_recent if not (j.get("applyStart") or j.get("applyEnd")))
    missing_work = sum(1 for j in support_recent if not (j.get("workStart") or j.get("workEnd")))
    payload["jobs"] = jobs
    payload["periodEnrichment"] = {
        "checkedAt": NOW.strftime("%Y-%m-%d %H:%M KST"),
        "parserVersion": PARSER_VERSION,
        "recentSupportJobs": len(support_recent),
        "detailPagesParsed": parsed_jobs,
        "fieldsFilledThisRun": filled_fields,
        "missingApplicationPeriod": missing_apply,
        "missingWorkPeriod": missing_work,
        "pendingDetailPages": pending_count,
        "displayFallback": "원문 확인",
    }
    JOBS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    patched = patch_frontend()
    print("PERIOD ENRICH result", payload["periodEnrichment"], "frontendPatched=", patched)


if __name__ == "__main__":
    main()
