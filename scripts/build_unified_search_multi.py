#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import build_unified_search as base
from private_source_registry import PRIVATE_SOURCES, publication_enabled, source_health
from source_registry import official_source_count

KST = timezone(timedelta(hours=9))

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

    explicit_provinces = [str(x) for x in (job.get("provinces") or []) if str(x)]
    if explicit_provinces:
        row["provinces"] = list(dict.fromkeys(explicit_provinces))
        if row.get("province") not in row["provinces"]:
            row["province"] = row["provinces"][0]
    else:
        row["provinces"] = [row.get("province")] if row.get("province") else []

    explicit_regions = [str(x) for x in (job.get("regions") or []) if str(x)]
    if explicit_regions:
        row["regions"] = list(dict.fromkeys(explicit_regions))
        row["region"] = str(job.get("region") or row["region"] or row["regions"][0])

    row["searchText"] = base.norm(" ".join(map(str, [
        row.get("school"), row.get("title"), row.get("subject"), row.get("region"),
        " ".join(row.get("regions") or []), row.get("province"),
        " ".join(row.get("provinces") or []), row.get("location"), row.get("source"),
        row.get("sourceSurfaceLabel"), row.get("type"), " ".join(row.get("categories") or [])
    ])))
    return row


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
    degraded_private_sources = []
    for spec in PRIVATE_SOURCES:
        pdata = load(spec["jobs"], [])
        preport = load(spec["report"], {})
        dreport = load(spec["detail_report"], {}) if spec.get("detail_report") else None
        jobs = rows_from(pdata)
        projected = [project_private_generic(j, spec["name"]) for j in jobs if base.private_current(j)]
        configured_enabled = publication_enabled(preport)
        healthy = source_health(spec, preport, dreport)
        effective_enabled = configured_enabled and healthy
        if effective_enabled:
            enabled_private_sources += 1
            canonical_private_total += len(jobs)
            all_private.extend(projected)
        elif configured_enabled and not healthy:
            degraded_private_sources.append(spec["key"])
        private_meta[spec["key"]] = {
            "name": spec["name"],
            "configuredPublicationEnabled": configured_enabled,
            "publicationEnabled": effective_enabled,
            "degraded": configured_enabled and not healthy,
            "ok": healthy,
            "candidateCount": len(projected),
            "count": len(projected) if effective_enabled else 0,
            "lastVerifiedAt": (dreport or preport).get("generatedAt") if isinstance((dreport or preport), dict) else None,
            "missingAfterCount": preport.get("missingAfterCount") if isinstance(preport, dict) else None,
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

    expected_official_sources = official_source_count()
    embedded_official_sources = official_data.get("officialSourceCount") if isinstance(official_data, dict) else None
    if embedded_official_sources is not None and int(embedded_official_sources) != expected_official_sources:
        raise SystemExit(
            f"jobs.json officialSourceCount={embedded_official_sources} does not match sources.json={expected_official_sources}"
        )

    payload = {
        "updatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "dataset": "unified-search-v3-multi-private",
        "officialSourceCount": expected_official_sources,
        "totalSourceCount": expected_official_sources + enabled_private_sources,
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
        "degradedPrivateSources": degraded_private_sources,
        "allPublicationEnabledSourcesHealthy": not degraded_private_sources,
    }
    Path("unified_search_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
