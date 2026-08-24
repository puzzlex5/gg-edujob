#!/usr/bin/env python3
"""Merge a freshly re-crawled snapshot with the last published dataset without silently dropping jobs.

Freshly collected records win. Previous records are carried forward when they are still
active/recent, or when the latest independent official-ID reconciliation says that exact posting
ID was present in the latest official scan. This lets a fast/current-page crawl stay incremental
without destroying the stronger 90-day completeness proof produced by reconciliation.

Before merge/dedupe, support-office source labels are normalized from the exact official URL host.
Stable official source IDs are preferred over fuzzy/title identity so distinct official postings
are never collapsed merely because their titles look alike.
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


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        if default is not None:
            return default
        raise


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
        for key in (
            "pbancSn", "q_rcrtSn", "bbsId", "nttSn", "mi", "job_seq", "seq", "clasHmpgId"
        ):
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


def stable_source_id(job):
    """Same official identity scheme used by reconcile_source_ids.py/update_collector_status.py."""
    raw = str(job.get("url") or "")
    try:
        parsed = urlparse(raw)
        q = parse_qs(parsed.query)
    except Exception:
        parsed = urlparse("")
        q = {}

    if job.get("sourceType") == "통합게시판":
        pb = str((q.get("pbancSn") or [""])[0])
        if pb.isdigit():
            return f"goe-central:{pb}"
        rid = str((q.get("q_rcrtSn") or [""])[0])
        if rid.isdigit():
            return f"seoul-central:{rid}"

    province = str(job.get("province") or "")
    if province == "서울":
        seq = str((job.get("openParams") or {}).get("job_seq") or "")
        open_raw = str(job.get("openUrl") or raw)
        host = (urlparse(open_raw).hostname or "").lower()
        if not seq.isdigit():
            seq = str((q.get("job_seq") or [""])[0])
        if host and seq.isdigit():
            return f"seoul:{host}:{seq}"

    if province == "경기":
        host = (parsed.hostname or "").lower()
        bbs = str(job.get("bbsId") or (q.get("bbsId") or [""])[0])
        ntt = str(job.get("nttSn") or (q.get("nttSn") or [""])[0])
        if host and bbs.isdigit() and ntt.isdigit():
            return f"mircms:{host}:{bbs}:{ntt}"
    return ""


def key(job):
    sid = stable_source_id(job)
    if sid:
        return ("official", sid)
    url = normalized_url(job.get("url"))
    if url:
        return ("url", url)
    return (
        "fallback",
        norm_text(job.get("province")), norm_text(job.get("source")),
        norm_text(job.get("school")), norm_text(job.get("title")),
        norm_text(job.get("registered")),
    )


def latest_official_ids(ledger):
    entries = ledger.get("entries", {}) if isinstance(ledger, dict) else {}
    return {
        sid for sid, entry in entries.items()
        if isinstance(entry, dict) and entry.get("presentInLatestOfficialScan") is True
    }


def should_carry(job, protected_official_ids):
    sid = stable_source_id(job)
    if sid and sid in protected_official_ids:
        return True, "present in latest independent official-ID reconciliation"
    if EXCLUDE_WORDS.search(str(job.get("title") or "")):
        return False, ""
    end = parse_date(job.get("applyEnd"))
    if end and end >= TODAY:
        return True, "previously published posting still within application period"
    registered = parse_date(job.get("registered"))
    if registered and registered >= TODAY - timedelta(days=30):
        return True, "previously published recent posting not rediscovered in this crawl"
    return False, ""


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
    ap.add_argument("--source-id-ledger", default="source_id_ledger.json")
    args = ap.parse_args()

    previous = load(args.previous)
    current = load(args.current)
    sources = load(args.sources)
    ledger = load(args.source_id_ledger, {"entries": {}})
    protected_official_ids = latest_official_ids(ledger)
    host_map = build_host_map(sources)
    prev_jobs = previous.get("jobs", []) if isinstance(previous, dict) else []
    cur_jobs = current.get("jobs", []) if isinstance(current, dict) else []

    # Normalize both sides before identity construction and carry-forward reporting.
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
    protected_carried = 0
    for old in prev_jobs:
        k = key(old)
        if k in positions:
            before = json.dumps(merged[positions[k]], ensure_ascii=False, sort_keys=True)
            merged[positions[k]] = fill_missing(merged[positions[k]], old)
            after = json.dumps(merged[positions[k]], ensure_ascii=False, sort_keys=True)
            if before != after:
                enriched += 1
            continue
        carry, reason = should_carry(old, protected_official_ids)
        if not carry:
            continue
        copy = dict(old)
        normalize_support_source(copy, host_map)
        copy["recoveryCarryForward"] = True
        copy["recoveryReason"] = reason
        sid = stable_source_id(copy)
        if sid and sid in protected_official_ids:
            copy["reconciliationCarryForward"] = True
            protected_carried += 1
        positions[k] = len(merged)
        merged.append(copy)
        carried.append({
            "province": copy.get("province"), "source": copy.get("source"),
            "school": copy.get("school"), "title": copy.get("title"),
            "registered": copy.get("registered"), "applyEnd": copy.get("applyEnd"),
            "sourceIdentity": sid, "reason": reason, "url": copy.get("url"),
        })

    current["jobs"] = merged
    current["missingRecovery"] = {
        "freshCount": len(cur_jobs),
        "previousCount": len(prev_jobs),
        "mergedCount": len(merged),
        "carriedForward": len(carried),
        "officialIdProtected": len(protected_official_ids),
        "officialIdCarryForward": protected_carried,
        "enrichedFromPrevious": enriched,
        "policy": (
            "fresh wins; latest official reconciliation IDs survive incremental crawl gaps; "
            "otherwise active/recent previous recruitment postings survive one-run disappearance"
        ),
        "generatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
    }
    Path(args.current).write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.report).write_text(
        json.dumps({"summary": current["missingRecovery"], "carried": carried}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(current["missingRecovery"], ensure_ascii=False))


if __name__ == "__main__":
    main()
