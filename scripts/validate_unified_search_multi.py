#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")
BANNED = re.compile(r"구직|학원\s*매매|악기\s*(?:판매|매매)|연습실|원생\s*모집|학생\s*모집|레슨생\s*모집|팝니다|삽니다|권리금|임대", re.I)
PROMO_ONLY = re.compile(r"(?:홍보|광고)\s*(?:글|게시글|게시|합니다|드립니다|안내)$", re.I)
SPECS = [
    {"key":"lessoninfo","name":"레슨인포","jobs":"lessoninfo_jobs.json","report":"lessoninfo_reconciliation_report.json","detail_report":None},
    {"key":"jobteacher","name":"잡티처","jobs":"jobteacher_jobs.json","report":"jobteacher_reconciliation_report.json","detail_report":"jobteacher_detail_link_report.json"},
    {"key":"artmore","name":"아트모아","jobs":"artmore_jobs.json","report":"artmore_reconciliation_report.json","detail_report":"artmore_detail_link_report.json"},
]


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


def main():
    data = load("unified_jobs.next.json", {})
    jobs = data.get("jobs", [])
    private = [j for j in jobs if j.get("feedKind") == "private"]
    official = [j for j in jobs if j.get("feedKind") == "official"]
    errors, warnings = [], []

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
    non_metro = banned = expired = future = 0
    for spec in SPECS:
        src = rows_from(load(spec["jobs"], []))
        rep = load(spec["report"], {})
        drep = load(spec["detail_report"], {}) if spec.get("detail_report") else None
        source_ids = {str(j.get("sourceIdentity") or "") for j in src if j.get("sourceIdentity")}
        source_ids_union |= source_ids
        source_reports[spec["key"]] = {"count":len(src),"report":rep,"detailReport":drep}
        if not rep.get("healthy") or not rep.get("traversalComplete") or int(rep.get("missingAfterCount") or 0) != 0:
            errors.append(f"{spec['name']} source reconciliation is not healthy/complete")
        if spec["key"] == "lessoninfo" and int(rep.get("detailErrorCount") or 0) != 0:
            errors.append(f"{spec['name']} detailErrorCount={rep.get('detailErrorCount')}")
        if drep is not None and (not drep.get("healthy") or not drep.get("detailCoverageComplete") or int(drep.get("detailErrorCount") or 0) != 0):
            errors.append(f"{spec['name']} detail link validation is not healthy/complete")
        missing = sorted(source_ids - represented)
        missing_by_source[spec["key"]] = missing
        if missing:
            errors.append(f"Unified dataset dropped {len(missing)} current {spec['name']} stable IDs")
        non_metro += sum(1 for j in src if j.get("province") not in {"서울","경기"})
        banned += sum(1 for j in src if BANNED.search(str(j.get("title") or "")) or PROMO_ONLY.search(str(j.get("title") or "")))
        expired += sum(1 for j in src if parse_date(j.get("applyEnd")) and parse_date(j.get("applyEnd")) < TODAY)
        future += sum(1 for j in src if parse_date(j.get("registered")) and parse_date(j.get("registered")) > TODAY)

    extra_private = sorted(represented - source_ids_union)
    if extra_private: errors.append(f"Unified dataset invented {len(extra_private)} private stable IDs")
    if non_metro: errors.append(f"Canonical private datasets contain {non_metro} non-Seoul/Gyeonggi rows")
    if banned: errors.append(f"Canonical private datasets contain {banned} banned non-recruitment titles")
    if expired: errors.append(f"Canonical private datasets contain {expired} expired postings")
    if future: errors.append(f"Canonical private datasets contain {future} future registration dates")
    if bad_alias_evidence: errors.append(f"Unified dataset has {len(bad_alias_evidence)} aliases without strong exact evidence")

    ids = [str(j.get("sourceIdentity") or "") for j in jobs if j.get("sourceIdentity")]
    duplicate_ids = len(ids) - len(set(ids))
    if duplicate_ids: errors.append(f"Unified dataset has {duplicate_ids} duplicate primary stable identities")
    missing_links = [j for j in jobs if not (j.get("url") or j.get("originalUrl") or (j.get("openUrl") and j.get("openParams")))]
    if missing_links: errors.append(f"Unified dataset has {len(missing_links)} rows without usable links")
    no_search_text = [j for j in jobs if not str(j.get("searchText") or "").strip()]
    if no_search_text: errors.append(f"Unified dataset has {len(no_search_text)} rows without searchText")

    counts = data.get("counts", {})
    if int(counts.get("private", -1)) != len(private) or int(counts.get("official", -1)) != len(official):
        errors.append("Embedded unified display counts disagree with actual rows")
    expected_total_sources = 38 + len(SPECS)
    if int(data.get("officialSourceCount") or 0) != 38:
        warnings.append(f"officialSourceCount={data.get('officialSourceCount')} expected 38")
    if int(data.get("totalSourceCount") or 0) < expected_total_sources:
        errors.append(f"Unified source count does not include 38 official plus {len(SPECS)} private sources")
    private_meta = data.get("privateSources", {})
    for spec in SPECS:
        if not (private_meta.get(spec["key"]) or {}).get("ok"):
            errors.append(f"Embedded source health false for {spec['name']}")

    report = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "healthy": not errors,
        "total": len(jobs),
        "official": len(official),
        "privateDisplayed": len(private),
        "privateStableIds": len(source_ids_union),
        "privateRepresentedDirectly": len(direct_ids),
        "privateRepresentedByOfficial": len(alias_ids),
        "missingPrivateIds": sum(len(v) for v in missing_by_source.values()),
        "missingPrivateBySource": {k:len(v) for k,v in missing_by_source.items()},
        "missingPrivateExamples": {k:v[:20] for k,v in missing_by_source.items()},
        "nonMetroPrivate": non_metro,
        "bannedPrivate": banned,
        "expiredPrivate": expired,
        "futurePrivateDates": future,
        "duplicateStableIds": duplicate_ids,
        "missingLinks": len(missing_links),
        "badCrossSourceAliasEvidence": len(bad_alias_evidence),
        "errors": errors,
        "warnings": warnings,
    }
    Path("unified_validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors: raise SystemExit(3)


if __name__ == "__main__":
    main()
