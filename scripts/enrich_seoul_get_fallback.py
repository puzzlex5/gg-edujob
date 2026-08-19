#!/usr/bin/env python3
"""Retry Seoul support-office detail pages with GET ?job_seq= fallback.

Several Seoul JOV11 endpoints render a real individual posting when job_seq is
supplied in the query string even when the legacy POST request returns an empty
shell with HTTP 200.  The main enricher keeps the POST cache key used by jobs,
so this pass upgrades only failed/unavailable cache entries after independently
confirming the GET response contains the expected posting title.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import requests

import enrich_support_periods as base

ROOT = Path(__file__).resolve().parents[1]
JOBS_PATH = ROOT / "jobs.json"
CACHE_PATH = ROOT / "support_period_cache.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
UA = "Mozilla/5.0 (compatible; metro-edujob-seoul-get-fallback/1.0)"


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def title_tokens(title):
    # Long Korean/ASCII tokens are enough to distinguish a real detail page from
    # the blank JOV11 shell. Ignore generic recruitment words.
    generic = {"채용", "공고", "모집", "기간제", "교사", "강사", "학년도"}
    tokens = [t for t in re.findall(r"[0-9A-Za-z가-힣]{3,}", title or "") if t not in generic]
    return tokens[:8]


def detail_get_url(open_url, seq):
    p = urlparse(open_url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    q["job_seq"] = str(seq)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), p.fragment))


def fetch(job):
    key = base.fetch_key(job)
    seq = str((job.get("openParams") or {}).get("job_seq", ""))
    url = detail_get_url(job.get("openUrl", ""), seq)
    try:
        r = requests.get(
            url,
            headers={
                "User-Agent": UA,
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
                "Referer": job.get("boardUrl", ""),
            },
            timeout=18,
            allow_redirects=True,
        )
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        text = clean(r.text)
        tokens = title_tokens(job.get("title", ""))
        if tokens and not any(t in text for t in tokens):
            return key, None
        result = base.extract_periods(r.text)
        # Even if dates live only in an attachment, a title-confirmed detail page is
        # a valid page. Keep 'unavailable' rather than inventing dates.
        result["http"] = r.status_code
        result["checkedAt"] = NOW.strftime("%Y-%m-%d %H:%M KST")
        result["parserVersion"] = base.PARSER_VERSION
        result["transport"] = "GET-job_seq-fallback"
        result["detailUrl"] = url
        return key, result
    except Exception:
        return key, None


def main():
    payload = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    jobs = payload.get("jobs", [])

    candidates = {}
    for job in jobs:
        if job.get("province") != "서울" or job.get("sourceType") != "교육지원청 개별 게시판":
            continue
        if job.get("openMethod") != "POST" or not job.get("openUrl") or not (job.get("openParams") or {}).get("job_seq"):
            continue
        key = base.fetch_key(job)
        current = cache.get(key, {})
        missing = not any(job.get(k) for k in ("applyStart", "applyEnd", "workStart", "workEnd"))
        if missing or current.get("status") in {"unavailable", "error", "soft404", "no-detail-link"}:
            candidates.setdefault(key, job)

    improved = 0
    confirmed = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch, job) for job in list(candidates.values())[:500]]
        for fut in as_completed(futures):
            key, result = fut.result()
            if not key or result is None:
                continue
            confirmed += 1
            old = cache.get(key, {})
            old_score = sum(bool(old.get(k)) for k in ("applyStart", "applyEnd", "workStart", "workEnd"))
            new_score = sum(bool(result.get(k)) for k in ("applyStart", "applyEnd", "workStart", "workEnd"))
            # A title-confirmed GET page is preferable to an empty POST shell; retain
            # any fields the old parser already found.
            merged = dict(result)
            for field in ("applyStart", "applyEnd", "workStart", "workEnd"):
                if not merged.get(field) and old.get(field):
                    merged[field] = old[field]
            if any(merged.get(k) for k in ("applyStart", "applyEnd", "workStart", "workEnd")):
                merged["status"] = "parsed"
            cache[key] = merged
            if new_score > old_score or (old.get("status") != "parsed" and merged.get("status") == "parsed"):
                improved += 1

    # Apply upgraded cache values immediately to jobs so this run benefits before verification.
    filled = 0
    for job in jobs:
        key = base.fetch_key(job)
        result = cache.get(key, {})
        if not key or result.get("transport") != "GET-job_seq-fallback":
            continue
        for field in ("applyStart", "applyEnd", "workStart", "workEnd"):
            if not job.get(field) and result.get(field):
                job[field] = result[field]
                filled += 1
        if result.get("status"):
            job["periodStatus"] = result["status"]

    payload["jobs"] = jobs
    payload.setdefault("periodEnrichment", {})["seoulGetFallback"] = {
        "checkedAt": NOW.strftime("%Y-%m-%d %H:%M KST"),
        "candidates": len(candidates),
        "detailPagesConfirmed": confirmed,
        "cacheEntriesImproved": improved,
        "fieldsFilled": filled,
    }
    JOBS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SEOUL GET FALLBACK", payload["periodEnrichment"]["seoulGetFallback"])


if __name__ == "__main__":
    main()
