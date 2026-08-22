#!/usr/bin/env python3
"""Merge a freshly re-crawled snapshot with the last published dataset without silently dropping active jobs.

Freshly collected records win. A previously published record is carried forward only when it is
still plausibly active/recent and was not rediscovered in the fresh snapshot. This protects users
from transient parser/network gaps while avoiding indefinite retention of stale postings.

Before merge/dedupe, support-office source labels are normalized from the exact official URL host.
This prevents a historical merged label such as "평택교육지원청 + 안성교육지원청" from being
carried forward when the URL unambiguously belongs to one support office.
"""
import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from normalize_source_attribution import build_host_map, host_of

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")
EXCLUDE_WORDS = re.compile(
    r"최종\s*합격|합격자|서류\s*심사|서류전형|면접\s*대상|선정\s*결과|"
    r"채용\s*결과|전형\s*결과|합격\s*공고|인사\s*발령|발령\s*알림|발령\s*안내"
)


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


def normalize_support_source(job, host_map):
    """Normalize only provably support-office-owned URLs; preserve central portal labels."""
    raw_url = job.get("url") or job.get("openUrl") or job.get("boardUrl")
    canonical = host_map.get(host_of(raw_url))
    if not canonical:
        return job
    old = str(job.get("source") or "").strip()
    if old == canonical or "교육지원청" not in old:
        return job
    job["source"] = canonical
    return job


def key(job):
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
    if EXCLUDE_WORDS.search(str(job.get("title") or "")):
        return False
    end = parse_date(job.get("applyEnd"))
    if end:
        return end >= TODAY
    registered = parse_date(job.get("registered"))
    if registered:
        return registered >= TODAY - timedelta(days=30)
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
    ap.add_argument("--sources", default="sources.json")
    args = ap.parse_args()

    previous = load(args.previous)
    current = load(args.current)
    sources = load(args.sources)
    host_map = build_host_map(sources)
    prev_jobs = previous.get("jobs", []) if isinstance(previous, dict) else []
    cur_jobs = current.get("jobs", []) if isinstance(current, dict) else []

    # Normalize both sides before fallback identity construction and before carry-forward reporting.
    # Exact canonical URLs are stronger evidence than historical merged source strings.
    for job in cur_jobs:
        normalize_support_source(job, host_map)
    for job in prev_jobs:
        normalize_support_source(job, host_map)

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
        normalize_support_source(copy, host_map)
        copy["recoveryCarryForward"] = True
        copy["recoveryReason"] = "previously published active/recent posting not rediscovered in this crawl"
        positions[k] = len(merged)
        merged.append(copy)
        carried.append({
            "province": copy.get("province"), "source": copy.get("source"),
            "school": copy.get("school"), "title": copy.get("title"),
            "registered": copy.get("registered"), "applyEnd": copy.get("applyEnd"),
            "url": copy.get("url"),
        })

    current["jobs"] = merged
    current["missingRecovery"] = {
        "freshCount": len(cur_jobs),
        "previousCount": len(prev_jobs),
        "mergedCount": len(merged),
        "carriedForward": len(carried),
        "enrichedFromPrevious": enriched,
        "policy": "fresh wins; active/recent previous recruitment postings survive transient one-run disappearance; result/personnel notices are never resurrected",
        "generatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
    }
    Path(args.current).write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.report).write_text(json.dumps({"summary": current["missingRecovery"], "carried": carried}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(current["missingRecovery"], ensure_ascii=False))


if __name__ == "__main__":
    main()
