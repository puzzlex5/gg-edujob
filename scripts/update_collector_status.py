#!/usr/bin/env python3
"""Persist lightweight collector observability without touching published jobs.

Workflows call this before critical stages and on success/failure.  If a run dies,
the repository still records the last stage reached so silent automation failures
can be distinguished from a legitimate no-change refresh.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "collector_status.json"
KST = timezone(timedelta(hours=9))


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def jobs_count():
    data = load_json(ROOT / "jobs.json", {})
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    return len(jobs) if isinstance(jobs, list) else 0


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
