#!/usr/bin/env python3
"""Operational data-quality audit for 수도권에듀잡.

This is deliberately independent from the source crawler. It measures the published dataset
for duplicate stable IDs/URLs, likely non-recruitment notices, stale inactive records, source
freshness and population inflation against the 38-source reconciliation proof.

The audit is diagnostic-first: ambiguous semantic duplicates are never deleted. Exact stable-ID
or URL duplicates are surfaced separately so a repair workflow can act only on strong evidence.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
JOBS_PATH = ROOT / "jobs.json"
RECON_PATH = ROOT / "source_reconciliation_report.json"
REPORT_PATH = ROOT / "operational_quality_report.json"
HISTORY_PATH = ROOT / "operational_quality_history.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")
RESULT_RE = re.compile(r"최종\s*합격|합격자|서류\s*심사|서류전형\s*(?:결과|합격)|면접\s*대상|선정\s*결과|채용\s*결과|전형\s*결과|인사\s*발령")


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


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


def canonical_url(raw):
    if not raw:
        return ""
    try:
        p = urlparse(str(raw))
        q = parse_qs(p.query, keep_blank_values=True)
        keep = {}
        for key in ("pbancSn", "q_rcrtSn", "rcrtSn", "bbsId", "nttSn", "job_seq", "seq", "clasHmpgId"):
            if q.get(key):
                keep[key] = q[key][0]
        return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, "", urlencode(keep), ""))
    except Exception:
        return str(raw).strip()


def stable_id(job):
    params = job.get("openParams") or {}
    seq = str(params.get("job_seq") or "")
    host = (urlparse(job.get("openUrl") or job.get("url") or "").hostname or "").lower()
    if seq.isdigit():
        return f"seoul-support:{host}:{seq}"

    ntt = str(job.get("nttSn") or "")
    bbs = str(job.get("bbsId") or "")
    if ntt.isdigit() and bbs.isdigit():
        return f"mircms:{host}:{bbs}:{ntt}"

    for raw in (job.get("url"), job.get("openUrl")):
        try:
            p = urlparse(raw or "")
            q = parse_qs(p.query)
            pb = (q.get("pbancSn") or [""])[0]
            if pb.isdigit():
                return f"goe-central:{pb}"
            qr = (q.get("q_rcrtSn") or q.get("rcrtSn") or [""])[0]
            if str(qr).isdigit():
                return f"seoul-central:{qr}"
            ntt2 = (q.get("nttSn") or [""])[0]
            bbs2 = (q.get("bbsId") or [""])[0]
            if ntt2.isdigit() and bbs2.isdigit():
                return f"mircms:{(p.hostname or '').lower()}:{bbs2}:{ntt2}"
            seq2 = (q.get("job_seq") or [""])[0]
            if seq2.isdigit():
                return f"seoul-support:{(p.hostname or '').lower()}:{seq2}"
        except Exception:
            pass
    return ""


def sample(job):
    return {
        "province": job.get("province", ""),
        "source": job.get("source", ""),
        "school": job.get("school", ""),
        "title": job.get("title", ""),
        "registered": job.get("registered", ""),
        "applyEnd": job.get("applyEnd", ""),
        "url": job.get("url", ""),
    }


def main():
    payload = load(JOBS_PATH, {})
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    if len(jobs) < 100:
        raise SystemExit(f"Refusing quality audit on suspicious dataset: {len(jobs)}")

    recon = load(RECON_PATH, {})
    summary = recon.get("summary", {}) if isinstance(recon, dict) else {}
    official_count = int(summary.get("officialIdCount") or 0)

    by_id = defaultdict(list)
    by_url = defaultdict(list)
    semantic = defaultdict(list)
    source_jobs = defaultdict(list)
    result_like = []
    stale_inactive = []
    missing_detail = []

    today = NOW.date()
    for idx, job in enumerate(jobs):
        sid = stable_id(job)
        if sid:
            by_id[sid].append(idx)
        cu = canonical_url(job.get("url") or job.get("openUrl"))
        if cu:
            by_url[cu].append(idx)
        sem = "|".join((
            norm_text(job.get("province")), norm_text(job.get("school")),
            re.sub(r"[^0-9a-z가-힣]+", "", norm_text(job.get("title"))),
            norm_text(job.get("registered")),
        ))
        if sem.strip("|"):
            semantic[sem].append(idx)

        sources = set([str(job.get("source") or "").strip()])
        sources.update(str(x).strip() for x in (job.get("checkedSources") or []) if str(x).strip())
        for src in sources:
            if src:
                source_jobs[src].append(job)

        title = str(job.get("title") or "")
        if RESULT_RE.search(title):
            result_like.append(sample(job))

        reg = parse_date(job.get("registered"))
        end = parse_date(job.get("applyEnd"))
        if reg and reg < today - timedelta(days=120) and (not end or end < today):
            stale_inactive.append(sample(job))

        if not (job.get("url") or job.get("openUrl")):
            missing_detail.append(sample(job))

    exact_id_groups = {k: v for k, v in by_id.items() if len(v) > 1}
    exact_url_groups = {k: v for k, v in by_url.items() if len(v) > 1}
    semantic_groups = {k: v for k, v in semantic.items() if len(v) > 1}

    source_freshness = []
    recon_sources = recon.get("sources", []) if isinstance(recon, dict) else []
    for src in recon_sources:
        name = str(src.get("name") or "").strip()
        related = source_jobs.get(name, [])
        dates = [d for d in (parse_date(x.get("registered")) for x in related) if d]
        latest = max(dates) if dates else None
        age = (today - latest).days if latest else None
        source_freshness.append({
            "province": src.get("province", ""), "name": name,
            "officialIdCount": int(src.get("officialIdCount") or 0),
            "publishedRecords": len(related),
            "latestRegistered": latest.isoformat() if latest else "",
            "freshnessDays": age,
            "reconciled": bool(src.get("reconciled")),
            "missingAfterCount": int(src.get("missingAfterCount") or 0),
        })

    inflation = round(len(jobs) / official_count, 3) if official_count else None
    findings = []
    if exact_id_groups:
        findings.append({"severity": "warning", "code": "exact-stable-id-duplicates", "count": sum(len(v)-1 for v in exact_id_groups.values())})
    if exact_url_groups:
        findings.append({"severity": "warning", "code": "exact-detail-url-duplicates", "count": sum(len(v)-1 for v in exact_url_groups.values())})
    if result_like:
        findings.append({"severity": "warning", "code": "result-notice-contamination", "count": len(result_like)})
    if stale_inactive:
        findings.append({"severity": "warning", "code": "stale-inactive-overcollection", "count": len(stale_inactive)})
    if missing_detail:
        findings.append({"severity": "critical", "code": "missing-detail-link", "count": len(missing_detail)})
    if inflation is not None and inflation > 2.0:
        findings.append({"severity": "warning", "code": "population-inflation", "ratio": inflation})

    report = {
        "generatedAt": NOW.strftime("%Y-%m-%d %H:%M:%S KST"),
        "policy": "diagnostic-first; stable-ID/URL duplicates are strong evidence, semantic duplicates are review-only",
        "summary": {
            "publishedJobs": len(jobs),
            "officialIds": official_count,
            "publishedToOfficialRatio": inflation,
            "exactStableIdDuplicateGroups": len(exact_id_groups),
            "exactStableIdExtraRecords": sum(len(v)-1 for v in exact_id_groups.values()),
            "exactUrlDuplicateGroups": len(exact_url_groups),
            "exactUrlExtraRecords": sum(len(v)-1 for v in exact_url_groups.values()),
            "semanticDuplicateGroupsReviewOnly": len(semantic_groups),
            "resultLikeNotices": len(result_like),
            "staleInactiveOver120Days": len(stale_inactive),
            "missingDetailLinks": len(missing_detail),
            "reconciledSources": int(summary.get("reconciledSources") or 0),
            "totalSources": int(summary.get("totalSources") or 0),
            "missingOfficialIds": int(summary.get("missingAfter") or 0),
        },
        "findings": findings,
        "sourceFreshness": source_freshness,
        "examples": {
            "exactStableIdDuplicates": [
                {"stableId": k, "records": [sample(jobs[i]) for i in v[:4]]}
                for k, v in list(exact_id_groups.items())[:20]
            ],
            "exactUrlDuplicates": [
                {"url": k, "records": [sample(jobs[i]) for i in v[:4]]}
                for k, v in list(exact_url_groups.items())[:20]
            ],
            "resultLikeNotices": result_like[:20],
            "staleInactive": stale_inactive[:20],
            "missingDetailLinks": missing_detail[:20],
        },
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    history = load(HISTORY_PATH, {"runs": []})
    runs = history.get("runs", []) if isinstance(history, dict) else []
    runs.append({"generatedAt": report["generatedAt"], **report["summary"]})
    history = {"policy": "rolling 60 quality snapshots for anomaly detection", "runs": runs[-60:]}
    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report["summary"], ensure_ascii=False))
    if report["summary"]["missingOfficialIds"] > 0 or report["summary"]["reconciledSources"] < report["summary"]["totalSources"]:
        raise SystemExit("Official-source reconciliation is incomplete")
    if report["summary"]["missingDetailLinks"] > 0:
        raise SystemExit("Published dataset contains jobs without any detail/original URL")


if __name__ == "__main__":
    main()
