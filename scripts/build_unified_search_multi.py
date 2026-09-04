#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import build_unified_search as base

KST = timezone(timedelta(hours=9))
PRIVATE_SOURCES = [
    {
        "key": "lessoninfo",
        "name": "레슨인포",
        "jobs": "lessoninfo_jobs.json",
        "report": "lessoninfo_reconciliation_report.json",
        "detail_report": None,
    },
    {
        "key": "jobteacher",
        "name": "잡티처",
        "jobs": "jobteacher_jobs.json",
        "report": "jobteacher_reconciliation_report.json",
        "detail_report": "jobteacher_detail_link_report.json",
    },
    {
        "key": "artmore",
        "name": "아트모아",
        "jobs": "artmore_jobs.json",
        "report": "artmore_reconciliation_report.json",
        "detail_report": "artmore_detail_link_report.json",
    },
]

_BASE_CANONICAL_URL = base.canonical_url


def canonical_url_multi(raw):
    if not raw:
        return ""
    try:
        p = urlparse(str(raw))
        q = parse_qs(p.query, keep_blank_values=True)
        rec = str((q.get("rec_idx") or [""])[0])
        if rec.isdigit() and (p.hostname or "").lower().endswith("artmore.kr"):
            return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, "", urlencode({"rec_idx": rec}), ""))
    except Exception:
        pass
    return _BASE_CANONICAL_URL(raw)


base.canonical_url = canonical_url_multi


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def rows_from(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("jobs", [])
    return []


def project_private_generic(job, source_name):
    row = base.project_private(job)
    row["source"] = str(job.get("source") or source_name)
    row["sourceSurfaceLabel"] = str(job.get("sourceSurfaceLabel") or source_name)
    if row.get("school") == "레슨인포 구인" and source_name != "레슨인포":
        row["school"] = source_name + " 구인"
    row["searchText"] = base.norm(" ".join(map(str, [
        row.get("school"), row.get("title"), row.get("subject"), row.get("region"),
        row.get("province"), row.get("location"), row.get("source"),
        row.get("sourceSurfaceLabel"), row.get("type"), " ".join(row.get("categories") or [])
    ])))
    return row


def publication_enabled(report):
    return not isinstance(report, dict) or "publicationEnabled" not in report or report.get("publicationEnabled") is True


def source_health(spec, report, detail_report):
    ok = bool(report and report.get("healthy") and report.get("traversalComplete") and int(report.get("missingAfterCount") or 0) == 0)
    if spec["key"] == "lessoninfo":
        ok = ok and int(report.get("detailErrorCount") or 0) == 0
    if spec.get("detail_report"):
        ok = ok and bool(detail_report and detail_report.get("healthy") and detail_report.get("detailCoverageComplete") and int(detail_report.get("detailErrorCount") or 0) == 0)
    return ok


def main():
    official_data = load("jobs.json", {})
    official_jobs = rows_from(official_data)
    ledger = load("source_id_ledger.json", {"entries": {}})
    protected = base.latest_official_ids(ledger)
    projected_official = [base.project_official(j) for j in official_jobs if base.official_current(j, protected)]

    all_private = []
    private_meta = {}
    canonical_private_total = 0
    enabled_private_sources = 0
    all_sources_healthy = True
    for spec in PRIVATE_SOURCES:
        pdata = load(spec["jobs"], [])
        preport = load(spec["report"], {})
        dreport = load(spec["detail_report"], {}) if spec.get("detail_report") else None
        jobs = rows_from(pdata)
        projected = [project_private_generic(j, spec["name"]) for j in jobs if base.private_current(j)]
        enabled = publication_enabled(preport)
        healthy = source_health(spec, preport, dreport)
        if enabled:
            enabled_private_sources += 1
            canonical_private_total += len(jobs)
            all_private.extend(projected)
            all_sources_healthy = all_sources_healthy and healthy
        private_meta[spec["key"]] = {
            "name": spec["name"],
            "publicationEnabled": enabled,
            "ok": healthy if enabled else False,
            "candidateCount": len(projected),
            "count": len(projected) if enabled else 0,
            "lastVerifiedAt": (dreport or preport).get("generatedAt") if isinstance((dreport or preport), dict) else None,
            "missingAfterCount": preport.get("missingAfterCount"),
            "detailErrorCount": (dreport or preport).get("detailErrorCount") if isinstance((dreport or preport), dict) else None,
        }

    remaining_private, explicit_aliases, ambiguous_aliases = base.merge_explicit_official_aliases(projected_official, all_private)
    rows, exact_url_groups = base.dedupe_strong(projected_official + remaining_private)
    rows.sort(key=lambda j: (j.get("registered") or "", j.get("applyEnd") or "9999-12-31", j.get("sourceIdentity") or ""), reverse=True)

    per_feed = {"official": 0, "private": 0}
    per_private_source_displayed = {spec["key"]: 0 for spec in PRIVATE_SOURCES}
    for j in rows:
        kind = j.get("feedKind", "official")
        per_feed[kind] = per_feed.get(kind, 0) + 1
        if kind == "private":
            src = j.get("source")
            for spec in PRIVATE_SOURCES:
                if src == spec["name"]:
                    per_private_source_displayed[spec["key"]] += 1
    for spec in PRIVATE_SOURCES:
        private_meta[spec["key"]]["displayedAsPrivate"] = per_private_source_displayed[spec["key"]]

    official_source_count = int(official_data.get("officialSourceCount", 38)) if isinstance(official_data, dict) else 38
    payload = {
        "updatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "dataset": "unified-search-v3-multi-private",
        "officialSourceCount": official_source_count,
        "totalSourceCount": official_source_count + enabled_private_sources,
        "sources": official_data.get("sources", {}) if isinstance(official_data, dict) else {},
        "privateSources": private_meta,
        "counts": {
            "total": len(rows),
            **per_feed,
            "privateSourceOccurrences": {spec["key"]: private_meta[spec["key"]]["count"] for spec in PRIVATE_SOURCES},
        },
        "jobs": rows,
    }
    Path("unified_jobs.next.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    report = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "policy": "unified-search-v3-multi-private-strong-evidence-only",
        "canonicalOfficialJobs": len(official_jobs),
        "canonicalPrivateJobs": canonical_private_total,
        "selectedOfficialJobs": len(projected_official),
        "selectedPrivateJobs": len(all_private),
        "publishedJobs": len(rows),
        "perFeed": per_feed,
        "privateSources": private_meta,
        "protectedOfficialIds": len(protected),
        "explicitOfficialAliasGroupsMerged": len(explicit_aliases),
        "exactUrlAliasGroupsMerged": len(exact_url_groups),
        "ambiguousExplicitOfficialLinks": len(ambiguous_aliases),
        "semanticDuplicatePolicy": "review-only-never-auto-delete",
        "allPrivateSourcesHealthy": all_sources_healthy,
    }
    Path("unified_search_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not all_sources_healthy:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
