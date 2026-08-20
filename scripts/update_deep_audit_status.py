#!/usr/bin/env python3
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "deep_audit_status.json"
KST = timezone(timedelta(hours=9))

stage = sys.argv[1] if len(sys.argv) > 1 else "unknown"
state = sys.argv[2] if len(sys.argv) > 2 else "running"
now = datetime.now(KST).isoformat(timespec="seconds")

try:
    data = json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.exists() else {}
except Exception:
    data = {}

jobs_count = None
try:
    payload = json.loads((ROOT / "jobs.json").read_text(encoding="utf-8"))
    jobs_count = len(payload.get("jobs", [])) if isinstance(payload, dict) else None
except Exception:
    pass

entry = {
    "state": state,
    "stage": stage,
    "updatedAt": now,
    "jobsCount": jobs_count,
    "artifacts": {
        "coverageReport": (ROOT / "support_coverage_report.json").exists(),
        "linkResolution": (ROOT / "support_link_resolution.json").exists(),
        "jobLedger": (ROOT / "job_ledger.json").exists(),
        "collectionState": (ROOT / "collection_state.json").exists(),
        "boardDiscoveryReport": (ROOT / "board_discovery_report.json").exists(),
        "dailyRecoveryReport": (ROOT / "daily_missing_recovery_report.json").exists(),
    },
}
if state == "success":
    entry["lastSuccessAt"] = now
    if isinstance(data, dict) and data.get("lastFailureAt"):
        entry["lastFailureAt"] = data["lastFailureAt"]
elif state == "failed":
    entry["lastFailureAt"] = now
    if isinstance(data, dict) and data.get("lastSuccessAt"):
        entry["lastSuccessAt"] = data["lastSuccessAt"]
else:
    if isinstance(data, dict):
        for key in ("lastSuccessAt", "lastFailureAt"):
            if data.get(key): entry[key] = data[key]

STATUS.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"deep audit status: {state} @ {stage}; jobs={jobs_count}")
