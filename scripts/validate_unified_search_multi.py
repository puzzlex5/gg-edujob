#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_unified_search import RESULT_RE, private_current
from private_source_registry import PRIVATE_SOURCES, publication_enabled, source_health
from source_registry import official_source_count

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")
BANNED = re.compile(r"구직|학원\s*매매|악기\s*(?:판매|매매)|연습실|원생\s*모집|학생\s*모집|레슨생\s*모집|팝니다|삽니다|권리금|임대", re.I)
PROMO_ONLY = re.compile(r"(?:홍보|광고)\s*(?:글|게시글|게시|합니다|드립니다|안내)$", re.I)
ALLOWED_PROVINCES = {"서울", "경기"}


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def rows_from(data):
    if isinstance(data, list): return data
    if isinstance(data, dict): return data.get("jobs", [])
    return []


def parse_date(v):
    m = DATE_RE.search(str(v or ""))
    if not m: return None
    try: return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=KST).date()
    except ValueError: return None


def row_provinces(row):
    explicit = [str(x) for x in (row.get("provinces") or []) if str(x)]
    if explicit:
        return set(explicit)
    scalar = str(row.get("province") or "")
    return {scalar} if scalar else set()


def main():
    data = load("unified_jobs.next.json", {})
    jobs = data.get("jobs", [])
    private = [j for j in jobs if j.get("feedKind") == "private"]
    official = [j for j in jobs if j.get("feedKind") == "official"]
    errors, warnings = [], []

    official_result_like = [j for j in official if RESULT_RE.search(str(j.get("title") or ""))]
    if official_result_like:
        errors.append(f"Unified official projection contains {len(official_result_like)} result/selection notices")

    direct_ids = {str(j.get("sourceIdentity") or "") for j in private if j.get("sourceIdentity")}
    alias_ids = set()
    bad_alias_evidence = []
    for row in official:
        for alias in row.get("alsoSeenOn") or []:
            sid = str(alias.get("sourceIdentity") or "")
            if not sid: continue
            alias_ids.add(sid)
            if alias.get("evidence") not in {"explicit-exact-official-url", "exact-same-detail-url"}:
                bad_alias_evidence.append({"officialId": row.get("sourceIdentity"), "privateId": sid, "evidence": alias.get("evidence")})
    represented = direct_ids | alias_ids

    source_reports = {}
    missing_by_source = {}
    source_ids_union = set()
    enabled_specs = []
    degraded_specs = []
    non_metro = banned = expired = future = 0
    for spec in PRIVATE_SOURCES:
        raw_src = rows_from(load(spec["jobs"], []))
        rep = load(spec["report"], {})
        drep = load(spec["detail_report"], {}) if spec.get("detail_report") else None
        configured_enabled = publication_enabled(rep)
        healthy = source_health(spec, rep, drep)
        effective_enabled = configured_enabled and healthy

        # The canonical source files may intentionally retain expired/history rows for audit
        # and reconciliation. The builder applies private_current() before projecting into the
        # unified search, so completeness and contamination checks must validate that same
        # current-only population rather than demanding that expired archive rows be displayed.
        src = [j for j in raw_src if private_current(j)] if effective_enabled else []
        source_reports[spec["key"]] = {
            "rawCount": len(raw_src),
            "currentCount": len(src),
            "report": rep,
            "detailReport": drep,
            "configuredPublicationEnabled": configured_enabled,
            "publicationEnabled": effective_enabled,
            "degraded": configured_enabled and not healthy,
        }
        if configured_enabled and not healthy:
            degraded_specs.append(spec)
            warnings.append(f"{spec['name']} is degraded and excluded from this unified publication")
        if not effective_enabled:
            missing_by_source[spec["key"]] = []
            continue

        enabled_specs.append(spec)
        source_ids = {str(j.get("sourceIdentity") or "") for j in src if j.get("sourceIdentity")}
        source_ids_union |= source_ids
        missing = sorted(source_ids - represented)
        missing_by_source[spec["key"]] = missing
        if missing:
            errors.append(f"Unified dataset dropped {len(missing)} current {spec['name']} stable IDs")

        for j in src:
            ps = row_provinces(j)
            if not ps or not ps.issubset(ALLOWED_PROVINCES):
                non_metro += 1
        banned += sum(1 for j in src if BANNED.search(str(j.get("title") or "")) or PROMO_ONLY.search(str(j.get("title") or "")))
        expired += sum(1 for j in src if parse_date(j.get("applyEnd")) and parse_date(j.get("applyEnd")) < TODAY)
        future += sum(1 for j in src if parse_date(j.get("registered")) and parse_date(j.get("registered")) > TODAY)

    extra_private = sorted(represented - source_ids_union)
    if extra_private: errors.append(f"Unified dataset invented {len(extra_private)} private stable IDs")
    if non_metro: errors.append(f"Publication-effective private datasets contain {non_metro} non-Seoul/Gyeonggi rows")
    if banned: errors.append(f"Publication-effective private datasets contain {banned} banned non-recruitment titles")
    if expired: errors.append(f"Publication-effective private datasets contain {expired} expired postings")
    if future: errors.append(f"Publication-effective private datasets contain {future} future registration dates")
    if bad_alias_evidence: errors.append(f"Unified dataset has {len(bad_alias_evidence)} aliases without strong exact evidence")

    ids = [str(j.get("sourceIdentity") or "") for j in jobs if j.get("sourceIdentity")]
    duplicate_ids = len(ids) - len(set(ids))
    if duplicate_ids: errors.append(f"Unified dataset has {duplicate_ids} duplicate primary stable identities")
    missing_links = [j for j in jobs if not (j.get("url") or j.get("originalUrl") or (j.get("openUrl") and j.get("openParams")))]
    if missing_links: errors.append(f"Unified dataset has {len(missing_links)} rows without usable links")
    no_search_text = [j for j in jobs if not str(j.get("searchText") or "").strip()]
    if no_search_text: errors.append(f"Unified dataset has {len(no_search_text)} rows without searchText")

    projected_non_metro = [j for j in private if not row_provinces(j) or not row_provinces(j).issubset(ALLOWED_PROVINCES)]
    if projected_non_metro:
        errors.append(f"Unified private projection contains {len(projected_non_metro)} non-metro province sets")

    counts = data.get("counts", {})
    if int(counts.get("private", -1)) != len(private) or int(counts.get("official", -1)) != len(official):
        errors.append("Embedded unified display counts disagree with actual rows")

    expected_official_sources = official_source_count()
    expected_total_sources = expected_official_sources + len(enabled_specs)
    if int(data.get("officialSourceCount") or 0) != expected_official_sources:
        errors.append(
            f"officialSourceCount={data.get('officialSourceCount')} expected {expected_official_sources} from sources.json"
        )
    if int(data.get("totalSourceCount") or 0) != expected_total_sources:
        errors.append(
            f"Unified source count does not match {expected_official_sources} official plus {len(enabled_specs)} publication-effective private sources"
        )

    private_meta = data.get("privateSources", {})
    for spec in PRIVATE_SOURCES:
        meta = private_meta.get(spec["key"]) or {}
        expected_enabled = spec in enabled_specs
        expected_degraded = spec in degraded_specs
        if bool(meta.get("publicationEnabled")) != expected_enabled:
            errors.append(f"Embedded effective publication gate disagrees for {spec['name']}")
        if bool(meta.get("degraded")) != expected_degraded:
            errors.append(f"Embedded degraded state disagrees for {spec['name']}")
        if expected_enabled and not meta.get("ok"):
            errors.append(f"Embedded source health false for {spec['name']}")
        if not expected_enabled and int(meta.get("count") or 0) != 0:
            errors.append(f"Publication-disabled/degraded source {spec['name']} contributes rows to unified search")

    report = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "healthy": not errors,
        "total": len(jobs),
        "official": len(official),
        "privateDisplayed": len(private),
        "enabledPrivateSources": [spec["key"] for spec in enabled_specs],
        "degradedPrivateSources": [spec["key"] for spec in degraded_specs],
        "privateStableIds": len(source_ids_union),
        "privateRepresentedDirectly": len(direct_ids),
        "privateRepresentedByOfficial": len(alias_ids),
        "missingPrivateIds": sum(len(v) for v in missing_by_source.values()),
        "missingPrivateBySource": {k:len(v) for k,v in missing_by_source.items()},
        "missingPrivateExamples": {k:v[:20] for k,v in missing_by_source.items()},
        "officialResultLikeNotices": len(official_result_like),
        "nonMetroPrivate": non_metro,
        "projectedNonMetroPrivate": len(projected_non_metro),
        "bannedPrivate": banned,
        "expiredPrivate": expired,
        "futurePrivateDates": future,
        "duplicateStableIds": duplicate_ids,
        "missingLinks": len(missing_links),
        "badCrossSourceAliasEvidence": len(bad_alias_evidence),
        "sourceValidationPopulations": {
            key: {"rawCount": info["rawCount"], "currentCount": info["currentCount"]}
            for key, info in source_reports.items()
        },
        "errors": errors,
        "warnings": warnings,
    }
    Path("unified_validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors: raise SystemExit(3)


if __name__ == "__main__":
    main()
