#!/usr/bin/env python3
"""Run Lessoninfo collection with recruitment-only reconciliation semantics.

This keeps the collector's network/parsing primitives in crawl_lessoninfo_jobs.py while
ensuring intentionally rejected non-recruitment or out-of-window candidates are not
misclassified as missing recruitment postings.
"""
from __future__ import annotations

import json

import crawl_lessoninfo_jobs as c


# Keep historical seeds for backward compatibility, but also include the currently
# observed public recruitment boards. Lessoninfo has changed board identifiers over
# time, and treating an old board that returns HTTP 200 + zero rows as authoritative
# can otherwise create a silent empty traversal.
CURRENT_SEEDS = [
    {"bo_table": "20161213204337_3675", "code": "20170307205448_7050", "label": "학원강사구인-현재"},
    {"bo_table": "20170316171033_6494", "code": "20170307205423_8969", "label": "학교·방과후·늘봄-현재"},
    {"bo_table": "20170316171033_6494", "code": "", "label": "학교·방과후·늘봄-전체"},
]
for seed in CURRENT_SEEDS:
    if not any(
        existing.get("bo_table") == seed["bo_table"] and existing.get("code", "") == seed["code"]
        for existing in c.SEEDS
    ):
        c.SEEDS.append(seed)


def main() -> int:
    s = c.session()
    base = None
    robots_status = ""
    # Lessoninfo currently serves its board routes reliably on the www host. Prefer it
    # while retaining the bare host as a fallback.
    bases = ["https://www.lessoninfo.co.kr", "https://lessoninfo.co.kr"]
    bases.extend(candidate for candidate in c.BASES if candidate not in bases)
    for candidate in bases:
        allowed, status = c.check_robots(s, candidate)
        if allowed:
            try:
                r = c.get(s, candidate + "/")
                if r.ok:
                    base, robots_status = candidate, status
                    break
            except Exception:
                pass
        else:
            robots_status = status

    if not base:
        report = {
            "generatedAt": c.now_kst().isoformat(timespec="seconds"),
            "healthy": False,
            "blocker": "Lessoninfo is unavailable or robots policy does not allow this collector",
            "robotsStatus": robots_status,
            "preservedPreviousDataset": c.OUT.exists(),
        }
        c.write_json(c.REPORT, report)
        return 2

    all_candidates: dict[str, dict] = {}
    source_reports = []
    traversal_complete = True
    errors = []
    for seed in c.SEEDS:
        try:
            rows, sr = c.crawl_seed(s, base, seed)
            source_reports.append(sr)
            traversal_complete &= bool(sr["complete"])
            for row in rows:
                all_candidates.setdefault(row["sourceIdentity"], row)
        except Exception as e:
            traversal_complete = False
            source_reports.append({**seed, "complete": False, "error": f"{type(e).__name__}: {e}"})
            errors.append(f"{seed['bo_table']}:{seed.get('code','')}:{type(e).__name__}")

    previous = c.read_json(c.OUT, {"jobs": []})
    prev_jobs = previous if isinstance(previous, list) else previous.get("jobs", [])
    prev_by_id = {j.get("sourceIdentity"): j for j in prev_jobs if j.get("sourceIdentity")}
    jobs_by_id: dict[str, dict] = dict(prev_by_id)
    eligible_discovered_ids: set[str] = set()
    rejected_ids: set[str] = set()
    parsed_now = 0
    detail_errors = []

    for sid, candidate in all_candidates.items():
        try:
            job = c.parse_detail(s, candidate)
            if job:
                jobs_by_id[sid] = job
                eligible_discovered_ids.add(sid)
                parsed_now += 1
            else:
                # An explicitly rejected visible row is not a missing recruitment posting.
                # If it was previously published but has since been edited into a non-job,
                # remove that stale classification on a healthy traversal.
                rejected_ids.add(sid)
                jobs_by_id.pop(sid, None)
        except Exception as e:
            detail_errors.append({"id": sid, "error": type(e).__name__})
            if sid not in prev_by_id:
                traversal_complete = False

    dataset_ids = set(jobs_by_id)
    missing_after = sorted(eligible_discovered_ids - dataset_ids)
    # A complete-looking traversal that discovers zero candidates is not credible for
    # these active recruitment boards. Treat it as a structural/host failure so an empty
    # dataset can never become the new known-good state.
    empty_traversal = not all_candidates
    healthy = traversal_complete and not empty_traversal and not missing_after and not detail_errors

    if healthy:
        jobs = sorted(
            jobs_by_id.values(),
            key=lambda j: (j.get("registered") or "", j.get("sourceIdentity") or ""),
            reverse=True,
        )
        c.write_json(
            c.OUT,
            {
                "updatedAt": c.now_kst().strftime("%Y-%m-%d %H:%M KST"),
                "source": "레슨인포",
                "category": "private-recruitment",
                "jobs": jobs,
            },
        )
    else:
        jobs = prev_jobs

    ledger = c.read_json(c.LEDGER, {"ids": {}, "snapshots": []})
    ledger.setdefault("ids", {})
    for sid, item in all_candidates.items():
        ent = ledger["ids"].setdefault(sid, {"firstSeenAt": c.now_kst().isoformat(timespec="seconds")})
        ent["lastSeenAt"] = c.now_kst().isoformat(timespec="seconds")
        ent["url"] = item["url"]
        ent["classification"] = "recruitment" if sid in eligible_discovered_ids else "excluded"
    ledger.setdefault("snapshots", []).append(
        {
            "at": c.now_kst().isoformat(timespec="seconds"),
            "candidateIds": len(all_candidates),
            "discoveredIds": len(eligible_discovered_ids),
            "healthy": healthy,
        }
    )
    ledger["snapshots"] = ledger["snapshots"][-120:]
    c.write_json(c.LEDGER, ledger)

    report = {
        "generatedAt": c.now_kst().isoformat(timespec="seconds"),
        "policy": "lessoninfo-recruitment-only-stable-id-v2",
        "lookbackDays": c.LOOKBACK_DAYS,
        "base": base,
        "robotsStatus": robots_status,
        "healthy": healthy,
        "traversalComplete": traversal_complete,
        "sourceReports": source_reports,
        "candidateIdCount": len(all_candidates),
        "discoveredIdCount": len(eligible_discovered_ids),
        "publishedIdCount": len({j.get("sourceIdentity") for j in jobs if j.get("sourceIdentity")}),
        "missingAfterCount": len(missing_after),
        "missingAfterExamples": missing_after[:20],
        "parsedNow": parsed_now,
        "rejectedNonRecruitment": len(rejected_ids),
        "detailErrorCount": len(detail_errors),
        "detailErrorExamples": detail_errors[:10],
        "emptyTraversal": empty_traversal,
        "preservedPreviousDataset": not healthy,
        "errors": errors,
    }
    c.write_json(c.REPORT, report)
    c.write_json(
        c.STATE,
        {
            "updatedAt": c.now_kst().isoformat(timespec="seconds"),
            "healthy": healthy,
            "lastGoodAt": c.now_kst().isoformat(timespec="seconds") if healthy else c.read_json(c.STATE, {}).get("lastGoodAt"),
        },
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if healthy else 3


if __name__ == "__main__":
    raise SystemExit(main())
