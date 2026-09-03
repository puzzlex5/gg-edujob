#!/usr/bin/env python3
"""Fail-closed validation for the unified recruitment search projection."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")
BANNED = re.compile(r"구직|학원\s*매매|악기\s*(?:판매|매매)|연습실|원생\s*모집|학생\s*모집|레슨생\s*모집|팝니다|삽니다|권리금|임대|홍보", re.I)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_date(v):
    m = DATE_RE.search(str(v or ""))
    if not m: return None
    try: return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=KST).date()
    except ValueError: return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="unified_jobs.next.json")
    ap.add_argument("--lessoninfo", default="lessoninfo_jobs.json")
    ap.add_argument("--lessoninfo-report", default="lessoninfo_reconciliation_report.json")
    ap.add_argument("--report", default="unified_validation_report.json")
    args = ap.parse_args()

    data = load(args.file)
    lesson = load(args.lessoninfo)
    lreport = load(args.lessoninfo_report)
    jobs = data.get("jobs", [])
    private = [j for j in jobs if j.get("feedKind") == "private"]
    official = [j for j in jobs if j.get("feedKind") == "official"]
    lesson_jobs = lesson.get("jobs", []) if isinstance(lesson, dict) else lesson

    errors, warnings = [], []
    if not lreport.get("healthy") or not lreport.get("traversalComplete"):
        errors.append("Lessoninfo source reconciliation is not healthy/complete")
    if int(lreport.get("missingAfterCount") or 0) != 0:
        errors.append(f"Lessoninfo missingAfterCount={lreport.get('missingAfterCount')}")
    if int(lreport.get("detailErrorCount") or 0) != 0:
        errors.append(f"Lessoninfo detailErrorCount={lreport.get('detailErrorCount')}")

    source_private_ids = {str(j.get("sourceIdentity") or "") for j in lesson_jobs if j.get("sourceIdentity")}
    direct_private_ids = {str(j.get("sourceIdentity") or "") for j in private if j.get("sourceIdentity")}
    alias_private_ids = set()
    bad_alias_evidence = []
    for row in official:
        for alias in row.get("alsoSeenOn") or []:
            sid = str(alias.get("sourceIdentity") or "")
            if not sid:
                continue
            alias_private_ids.add(sid)
            if alias.get("source") == "레슨인포" and alias.get("evidence") not in {"explicit-exact-official-url", "exact-same-detail-url"}:
                bad_alias_evidence.append({"officialId": row.get("sourceIdentity"), "privateId": sid, "evidence": alias.get("evidence")})
    represented_private_ids = direct_private_ids | alias_private_ids
    missing_private = sorted(source_private_ids - represented_private_ids)
    extra_private = sorted(represented_private_ids - source_private_ids)
    if missing_private:
        errors.append(f"Unified dataset dropped {len(missing_private)} current Lessoninfo stable IDs")
    if extra_private:
        errors.append(f"Unified dataset invented {len(extra_private)} private stable IDs")
    if bad_alias_evidence:
        errors.append(f"Unified dataset has {len(bad_alias_evidence)} cross-source aliases without strong exact evidence")

    outside_source = [j for j in lesson_jobs if j.get("province") not in {"서울", "경기"}]
    banned_source = [j for j in lesson_jobs if BANNED.search(str(j.get("title") or ""))]
    expired_source = [j for j in lesson_jobs if parse_date(j.get("applyEnd")) and parse_date(j.get("applyEnd")) < TODAY]
    future_source = [j for j in lesson_jobs if parse_date(j.get("registered")) and parse_date(j.get("registered")) > TODAY]
    if outside_source:
        errors.append(f"Lessoninfo canonical dataset contains {len(outside_source)} non-Seoul/Gyeonggi rows")
    if banned_source:
        errors.append(f"Lessoninfo canonical dataset contains {len(banned_source)} banned non-recruitment titles")
    if expired_source:
        errors.append(f"Lessoninfo canonical dataset contains {len(expired_source)} expired postings")
    if future_source:
        errors.append(f"Lessoninfo canonical dataset contains {len(future_source)} future registration dates")

    ids = [str(j.get("sourceIdentity") or "") for j in jobs if j.get("sourceIdentity")]
    duplicate_ids = len(ids) - len(set(ids))
    if duplicate_ids:
        errors.append(f"Unified dataset has {duplicate_ids} duplicate primary stable identities")
    missing_links = [j for j in jobs if not (j.get("url") or j.get("originalUrl") or (j.get("openUrl") and j.get("openParams")))]
    if missing_links:
        errors.append(f"Unified dataset has {len(missing_links)} rows without a usable detail/original link")

    required_private_surfaces = {"afterschool-nulbom", "culture-arts"}
    source_surfaces = {j.get("sourceSurface") for j in lesson_jobs}
    if not required_private_surfaces.issubset(source_surfaces):
        errors.append(f"Lessoninfo source missing surface(s): {sorted(required_private_surfaces-source_surfaces)}")

    no_search_text = [j for j in jobs if not str(j.get("searchText") or "").strip()]
    if no_search_text:
        errors.append(f"Unified dataset has {len(no_search_text)} rows without precomputed searchText")
    representative = []
    for row in private[:10] + official[:10]:
        title = str(row.get("title") or "").strip()
        token = re.sub(r"[^0-9A-Za-z가-힣]+", " ", title).split()
        if token and token[0].lower() not in str(row.get("searchText") or ""):
            warnings.append(f"Representative title token not in searchText: {row.get('sourceIdentity')}")
        representative.append(row.get("sourceIdentity"))

    counts = data.get("counts", {})
    if int(counts.get("private") if counts.get("private") is not None else -1) != len(private) or int(counts.get("official") if counts.get("official") is not None else -1) != len(official):
        errors.append("Embedded unified display counts disagree with actual rows")
    if int(counts.get("lessoninfoSourceOccurrences") if counts.get("lessoninfoSourceOccurrences") is not None else len(source_private_ids)) != len(source_private_ids):
        errors.append("Embedded Lessoninfo source occurrence count disagrees with canonical Lessoninfo stable IDs")
    if int(data.get("officialSourceCount") or 0) != 38:
        warnings.append(f"officialSourceCount={data.get('officialSourceCount')} (expected current baseline 38)")
    if int(data.get("totalSourceCount") or 0) < 39:
        errors.append("Unified source count does not include 38 official sources plus Lessoninfo")

    report = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "healthy": not errors,
        "total": len(jobs),
        "official": len(official),
        "privateDisplayed": len(private),
        "lessoninfoStableIds": len(source_private_ids),
        "lessoninfoRepresentedDirectly": len(direct_private_ids),
        "lessoninfoRepresentedByOfficial": len(alias_private_ids),
        "missingLessoninfoIds": len(missing_private),
        "missingLessoninfoExamples": missing_private[:20],
        "nonMetroPrivate": len(outside_source),
        "bannedPrivate": len(banned_source),
        "expiredPrivate": len(expired_source),
        "futurePrivateDates": len(future_source),
        "duplicateStableIds": duplicate_ids,
        "missingLinks": len(missing_links),
        "badCrossSourceAliasEvidence": len(bad_alias_evidence),
        "representativeSearchChecks": representative,
        "errors": errors,
        "warnings": warnings[:30],
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
