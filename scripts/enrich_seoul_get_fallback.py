#!/usr/bin/env python3
"""Retry Seoul support-office detail pages with GET ?job_seq= fallback.

Several Seoul JOV11 endpoints render a real individual posting when job_seq is
supplied in the query string even when the legacy POST request returns an empty
shell with HTTP 200.  The main enricher keeps the POST cache key used by jobs,
so this pass upgrades only failed/unavailable cache entries after independently
confirming the GET response contains the expected posting title.

This pass also closes a timing gap in the deep audit: board discovery happens
before detail enrichment, while the standalone Seoul CMS recovery may have run
hours earlier. If discovery surfaced a CMS candidate that the last recovery
report has never evaluated, run the conservative CMS recovery immediately so
the same audit verifies and publishes it instead of waiting for a later refresh.
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
DISCOVERY_PATH = ROOT / "board_discovery_report.json"
CMS_REPORT_PATH = ROOT / "seoul_office_cms_recovery_report.json"
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


def recover_newly_discovered_cms():
    """Evaluate Seoul CMS candidates newly surfaced since the last recovery report.

    Compare URLs instead of filesystem mtimes because Git checkouts can refresh mtimes
    for unrelated files. A candidate already listed as accepted or skipped has been
    evaluated and does not need another immediate pass.
    """
    if not DISCOVERY_PATH.exists():
        return {"newCandidates": 0, "ran": False}
    try:
        discovery = json.loads(DISCOVERY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"newCandidates": 0, "ran": False, "reason": "discovery-unreadable"}

    discovered = {
        str(item.get("url") or "")
        for office in discovery.get("seoul", []) or []
        for item in office.get("candidates", []) or []
        if str(item.get("url") or "")
    }
    evaluated = set()
    if CMS_REPORT_PATH.exists():
        try:
            prior = json.loads(CMS_REPORT_PATH.read_text(encoding="utf-8"))
            for item in prior.get("accepted", []) or []:
                if item.get("url"):
                    evaluated.add(str(item["url"]))
            for item in prior.get("skipped", []) or []:
                if item.get("url"):
                    evaluated.add(str(item["url"]))
        except Exception:
            # An unreadable prior report must not suppress fresh official candidates.
            evaluated = set()

    unseen = sorted(discovered - evaluated)
    if not unseen:
        return {"newCandidates": 0, "ran": False}

    import recover_seoul_office_cms_jobs as cms

    cms.main()
    return {"newCandidates": len(unseen), "ran": True, "examples": unseen[:10]}


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

    cms_result = recover_newly_discovered_cms()
    payload["periodEnrichment"]["seoulCmsDiscoverySync"] = cms_result
    # Re-read because the CMS recovery may have appended jobs to jobs.json.
    current_payload = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    current_payload.setdefault("periodEnrichment", {})["seoulGetFallback"] = payload["periodEnrichment"]["seoulGetFallback"]
    current_payload["periodEnrichment"]["seoulCmsDiscoverySync"] = cms_result
    JOBS_PATH.write_text(json.dumps(current_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SEOUL GET FALLBACK", current_payload["periodEnrichment"]["seoulGetFallback"], "CMS SYNC", cms_result)


if __name__ == "__main__":
    main()
