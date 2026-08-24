#!/usr/bin/env python3
"""Annotate deep_audit_status.json with current effective health without erasing run history.

The raw deep-audit state/stage describes the last Daily deep recruitment audit run. A newer
verified fast/official-ID restoration can, however, prove that the currently published dataset is
complete even when that older run failed. Keep both facts: never rewrite the historical state,
but add effectiveState/effectiveProof when fresh completeness evidence is bound to jobs.json.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEEP = ROOT / "deep_audit_status.json"
COLLECTOR = ROOT / "collector_status.json"
KST = timezone(timedelta(hours=9))


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def parse_ts(value):
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except Exception:
        return None


def main():
    deep = load(DEEP, {})
    collector = load(COLLECTOR, {})
    if not isinstance(deep, dict):
        deep = {}
    if not isinstance(collector, dict):
        collector = {}

    candidates = []
    for workflow, entry in collector.items():
        if not isinstance(entry, dict) or entry.get("state") != "success":
            continue
        evidence = entry.get("coverageEvidence", {})
        if not isinstance(evidence, dict) or evidence.get("currentComplete") is not True:
            continue
        updated = parse_ts(entry.get("updatedAt"))
        if updated is None:
            continue
        candidates.append((updated, workflow, entry, evidence))

    if not candidates:
        deep["effectiveState"] = "unknown"
        deep["effectiveReason"] = "no successful collector has completeness evidence bound to the current dataset"
        DEEP.write_text(json.dumps(deep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("effective audit health: unknown")
        return

    updated, workflow, entry, evidence = max(candidates, key=lambda x: x[0])
    last_failure = parse_ts(deep.get("lastFailureAt"))
    supersedes_failure = bool(last_failure is None or updated > last_failure)

    rec = evidence.get("sourceReconciliation", {}) if isinstance(evidence, dict) else {}
    support = evidence.get("supportCoverage", {}) if isinstance(evidence, dict) else {}
    effective_proof = (
        "38-source-reconciliation"
        if isinstance(rec, dict) and rec.get("currentComplete") is True
        else evidence.get("proof", "")
    )
    deep.update({
        "effectiveState": "success" if supersedes_failure else "historical-failure-newer",
        "effectiveAt": updated.isoformat(timespec="seconds"),
        "effectiveWorkflow": workflow,
        "effectiveProof": effective_proof,
        "effectiveJobsCount": entry.get("jobsCount"),
        "effectiveSupersedesLastFailure": supersedes_failure,
        "effectiveEvidence": {
            "currentComplete": evidence.get("currentComplete") is True,
            "reconciledSources": rec.get("reconciledSources") if isinstance(rec, dict) else None,
            "totalSources": rec.get("totalSources") if isinstance(rec, dict) else None,
            "missingAfter": rec.get("missingAfter") if isinstance(rec, dict) else None,
            "missingOfficialIdsFromCurrentDataset": rec.get("missingOfficialIdsFromCurrentDataset") if isinstance(rec, dict) else None,
            "centralPaginationComplete": rec.get("centralPaginationComplete") if isinstance(rec, dict) else None,
            "supportCoverageCurrentComplete": support.get("currentComplete") if isinstance(support, dict) else None,
        },
    })
    DEEP.write_text(json.dumps(deep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"effective audit health: {deep['effectiveState']} via {workflow} ({effective_proof})")


if __name__ == "__main__":
    main()
