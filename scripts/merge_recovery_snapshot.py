#!/usr/bin/env python3
"""Merge a freshly re-crawled snapshot with the last published dataset without silently dropping active jobs.

Freshly collected records win. A previously published record is carried forward only when it is
still plausibly active/recent and was not rediscovered in the fresh snapshot. This protects users
from transient parser/network gaps while avoiding indefinite retention of stale postings.
"""
import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
LOOKBACK_DAYS = 90
DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def norm_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def parse_date(value):
    m = DATE_RE.search(str(value or ""))
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=KST).date()
    except ValueError:
        return None


def normalized_url(raw):
    if not raw:
        return ""
    try:
        p = urlparse(raw)
        q = parse_qs(p.query, keep_blank_values=True)
        keep = {}
        for key in ("bbsId", "nttSn", "mi", "job_seq", "seq", "clasHmpgId"):
            if q.get(key):
                keep[key] = q[key][0]
        query = urlencode(keep)
        return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, "", query, ""))
    except Exception:
        return str(raw).strip()


def key(job):
    # Prefer true posting identifiers over title matching.
    url = normalized_url(job.get("url"))
    if url and ("nttSn=" in url or "job_seq=" in url or "selectNttInfo.do" in url):
        return ("url", url)
    params = job.get("openParams") or {}
    seq = str(params.get("job_seq") or "")
    if seq.isdigit():
        return ("seoul", norm_text(job.get("openUrl")), seq)
    ntt = str(job.get("nttSn") or "")
    bbs = str(job.get("bbsId") or "")
    if ntt.isdigit() and bbs.isdigit():
        return ("goe", bbs, ntt)
    if url:
        return ("url", url)
    return (
        "fallback",
        norm_text(job.get("province")), norm_text(job.get("source")),
        norm_text(job.get("school")), norm_text(job.get("title")),
        norm_text(job.get("registered")),
    )


def should_carry(job):
    end = parse_date(job.get("applyEnd"))
    if end:
        return end >= TODAY
    registered = parse_date(job.get("registered"))
    if registered:
        # Unknown deadline: match the collector's full 90-day lookback. A temporarily missed
        # posting must not disappear merely because its deadline could not be parsed.
        return registered >= TODAY - timedelta(days=LOOKBACK_DAYS)
    return False


def fill_missing(fresh, old):
    for field in (
        "school", "subject", "region", "regions", "type", "schoolLevel",
        "applyStart", "applyEnd", "workStart", "workEnd", "registered", "headcount",
        "url", "boardUrl", "openMethod", "openUrl", "openParams", "nttSn", "bbsId", "mi",
    ):
        if fresh.get(field) in (None, "", [], {}) and old.get(field) not in (None, "", [], {}):
            fresh[field] = old[field]
    return fresh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--previous", required=True)
    ap.add_argument("--current", default="jobs.json")
    ap.add_argument("--report", default="missing_recovery_report.json")
    args = ap.parse_args()

    previous = load(args.previous)
    current = load(args.current)
    prev_jobs = previous.get("jobs", []) if isinstance(previous, dict) else []
    cur_jobs = current.get("jobs", []) if isinstance(current, dict) else []

    merged = []
    positions = {}
    for job in cur_jobs:
        k = key(job)
        if k in positions:
            merged[positions[k]] = fill_missing(merged[positions[k]], job)
            continue
        positions[k] = len(merged)
        merged.append(job)

    carried = []
    enriched = 0
    for old in prev_jobs:
        k = key(old)
        if k in positions:
            before = json.dumps(merged[positions[k]], ensure_ascii=False, sort_keys=True)
            merged[positions[k]] = fill_missing(merged[positions[k]], old)
            after = json.dumps(merged[positions[k]], ensure_ascii=False, sort_keys=True)
            if before != after:
                enriched += 1
            continue
        if not should_carry(old):
            continue
        copy = dict(old)
        copy["recoveryCarryForward"] = True
        copy["recoveryReason"] = "previously published active/recent posting not rediscovered in this crawl"
        positions[k] = len(merged)
        merged.append(copy)
        carried.append({
            "province": old.get("province"), "source": old.get("source"),
            "school": old.get("school"), "title": old.get("title"),
            "registered": old.get("registered"), "applyEnd": old.get("applyEnd"),
            "url": old.get("url"),
        })

    current["jobs"] = merged
    current["missingRecovery"] = {
        "freshCount": len(cur_jobs),
        "previousCount": len(prev_jobs),
        "mergedCount": len(merged),
        "carriedForward": len(carried),
        "enrichedFromPrevious": enriched,
        "unknownDeadlineCarryDays": LOOKBACK_DAYS,
        "policy": "fresh wins; active/recent previous postings survive transient one-run disappearance",
        "generatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
    }
    Path(args.current).write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.report).write_text(json.dumps({"summary": current["missingRecovery"], "carried": carried}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(current["missingRecovery"], ensure_ascii=False))


if __name__ == "__main__":
    main()
