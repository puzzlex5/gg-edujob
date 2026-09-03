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
    unified_private_ids = {str(j.get("sourceIdentity") or "") for j in private if j.get("sourceIdentity")}
    missing_private = sorted(source_private_ids - unified_private_ids)
    extra_private = sorted(unified_private_ids - source_private_ids)
    if missing_private:
        errors.append(f"Unified dataset dropped {len(missing_private)} current Lessoninfo stable IDs")
    if extra_private:
        errors.append(f"Unified dataset invented {len(extra_private)} private stable IDs")

    outside = [j for j in private if j.get("province") not in {"서울", "경기"}]
    if outside:
        errors.append(f"Private dataset contains {len(outside)} non-Seoul/Gyeonggi rows")
    banned = [j for j in private if BANNED.search(str(j.get("title") or ""))]
    if banned:
        errors.append(f"Private dataset contains {len(banned)} banned non-recruitment titles")
    expired = [j for j in private if parse_date(j.get("applyEnd")) and parse_date(j.get("applyEnd")) < TODAY]
    if expired:
        errors.append(f"Private dataset contains {len(expired)} expired postings")
    future = [j for j in private if parse_date(j.get("registered")) and parse_date(j.get("registered")) > TODAY]
    if future:
        errors.append(f"Private dataset contains {len(future)} future registration dates")

    ids = [str(j.get("sourceIdentity") or "") for j in jobs if j.get("sourceIdentity")]
    duplicate_ids = len(ids) - len(set(ids))
    if duplicate_ids:
        errors.append(f"Unified dataset has {duplicate_ids} duplicate stable identities")
    missing_links = [j for j in jobs if not (j.get("url") or j.get("originalUrl") or (j.get("openUrl") and j.get("openParams")))]
    if missing_links:
        errors.append(f"Unified dataset has {len(missing_links)} rows without a usable detail/original link")

    required_private_surfaces = {"afterschool-nulbom", "culture-arts"}
    seen_surfaces = {j.get("sourceSurface") for j in private}
    if not required_private_surfaces.issubset(seen_surfaces):
        errors.append(f"Unified private feed missing surface(s): {sorted(required_private_surfaces-seen_surfaces)}")

    # Search projection integrity: every row must have normalized searchable text and important fields included.
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
    if int(counts.get("private") or -1) != len(private) or int(counts.get("official") or -1) != len(official):
        errors.append("Embedded unified counts disagree with actual rows")
    if int(data.get("totalSourceCount") or 0) < 39:
        errors.append("Unified source count does not include 38 official sources plus Lessoninfo")

    report = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "healthy": not errors,
        "total": len(jobs),
        "official": len(official),
        "private": len(private),
        "lessoninfoStableIds": len(source_private_ids),
        "missingLessoninfoIds": len(missing_private),
        "missingLessoninfoExamples": missing_private[:20],
        "nonMetroPrivate": len(outside),
        "bannedPrivate": len(banned),
        "expiredPrivate": len(expired),
        "futurePrivateDates": len(future),
        "duplicateStableIds": duplicate_ids,
        "missingLinks": len(missing_links),
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
