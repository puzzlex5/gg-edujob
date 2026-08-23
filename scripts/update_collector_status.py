#!/usr/bin/env python3
"""Persist lightweight collector observability without touching published jobs.

Workflows call this before critical stages and on success/failure. If a run dies,
the repository still records the last stage reached so silent automation failures
can be distinguished from a legitimate no-change refresh.

Artifact existence alone is not proof of complete coverage: a fast refresh can
inherit a coverage report produced by an earlier deep audit. Record the report's
own timestamp, age, and 25+11 completeness evidence so monitors can distinguish a
fresh/known-good audit from a merely present stale file.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "collector_status.json"
KST = timezone(timedelta(hours=9))
# Daily deep audit normally refreshes this evidence every 24 hours. Allow a small
# scheduling/runtime margin, but never let a multi-day-old success masquerade as
# current completeness evidence.
COVERAGE_FRESH_HOURS = 30


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def jobs_count():
    data = load_json(ROOT / "jobs.json", {})
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    return len(jobs) if isinstance(jobs, list) else 0


def generated_at(path):
    data = load_json(path, {})
    return data.get("generatedAt", "") if isinstance(data, dict) else ""


def parse_timestamp(value):
    s = str(value or "").strip()
    if not s:
        return None
    candidates = [s]
    if s.endswith(" KST"):
        candidates.append(s[:-4] + "+09:00")
    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)
            return dt.astimezone(KST)
        except Exception:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S KST", "%Y-%m-%d %H:%M KST"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=KST)
        except Exception:
            pass
    return None


def coverage_evidence():
    report = load_json(ROOT / "support_coverage_report.json", {})
    summary = report.get("supportCompleteness", {}) if isinstance(report, dict) else {}
    links = report.get("supportLinkResolution", {}) if isinstance(report, dict) else {}
    warnings = summary.get("warnings", []) if isinstance(summary, dict) else []
    gyeonggi_complete = summary.get("gyeonggiComplete")
    gyeonggi_total = summary.get("gyeonggiTotal")
    seoul_complete = summary.get("seoulComplete")
    seoul_total = summary.get("seoulTotal")
    exact_links = (
        links.get("gyeonggiExact") == links.get("gyeonggiTotal")
        and links.get("seoulExact") == links.get("seoulTotal")
        and links.get("gyeonggiTotal") is not None
        and links.get("seoulTotal") is not None
    ) if isinstance(links, dict) else False
    complete = (
        gyeonggi_complete == 25
        and gyeonggi_total == 25
        and seoul_complete == 11
        and seoul_total == 11
        and not warnings
        and exact_links
    )
    generated = report.get("generatedAt", "") if isinstance(report, dict) else ""
    generated_dt = parse_timestamp(generated)
    age_hours = None
    fresh = False
    if generated_dt is not None:
        age_hours = max(0.0, (datetime.now(KST) - generated_dt).total_seconds() / 3600.0)
        fresh = age_hours <= COVERAGE_FRESH_HOURS
    return {
        "generatedAt": generated,
        "ageHours": round(age_hours, 2) if age_hours is not None else None,
        "freshWithinHours": COVERAGE_FRESH_HOURS,
        "fresh": fresh,
        "gyeonggiComplete": gyeonggi_complete,
        "gyeonggiTotal": gyeonggi_total,
        "seoulComplete": seoul_complete,
        "seoulTotal": seoul_total,
        "warnings": len(warnings) if isinstance(warnings, list) else None,
        "exactLinks": exact_links,
        # `complete` is historical truth about the report itself. `currentComplete`
        # is the operational signal monitors should use for the current system.
        "complete": complete,
        "currentComplete": bool(complete and fresh),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--stage")
    ap.add_argument("--state", choices=("running", "success", "failed"), required=True)
    args = ap.parse_args()

    all_status = load_json(STATUS, {})
    if not isinstance(all_status, dict):
        all_status = {}
    old = all_status.get(args.workflow, {})
    if not isinstance(old, dict):
        old = {}

    now = datetime.now(KST).isoformat(timespec="seconds")
    entry = dict(old)
    entry.update({
        "state": args.state,
        "updatedAt": now,
        "jobsCount": jobs_count(),
        "artifacts": {
            "jobLedger": (ROOT / "job_ledger.json").exists(),
            "collectionState": (ROOT / "collection_state.json").exists(),
            "coverageReport": (ROOT / "support_coverage_report.json").exists(),
            "missingRecoveryReport": (ROOT / "missing_recovery_report.json").exists(),
            "boardDiscoveryReport": (ROOT / "board_discovery_report.json").exists(),
        },
        "artifactGeneratedAt": {
            "coverageReport": generated_at(ROOT / "support_coverage_report.json"),
            "missingRecoveryReport": generated_at(ROOT / "missing_recovery_report.json"),
            "boardDiscoveryReport": generated_at(ROOT / "board_discovery_report.json"),
            "collectionState": generated_at(ROOT / "collection_state.json"),
        },
        "coverageEvidence": coverage_evidence(),
    })
    if args.stage:
        entry["stage"] = args.stage
    if args.state == "success":
        entry["lastSuccessAt"] = now
    elif args.state == "failed":
        entry["lastFailureAt"] = now

    all_status[args.workflow] = entry
    STATUS.write_text(json.dumps(all_status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"collector status: {args.workflow} {args.state} stage={entry.get('stage','')}")


if __name__ == "__main__":
    main()
