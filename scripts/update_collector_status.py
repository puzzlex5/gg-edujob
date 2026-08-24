#!/usr/bin/env python3
"""Persist lightweight collector observability without touching published jobs.

Workflows call this before critical stages and on success/failure. If a run dies,
the repository still records the last stage reached so silent automation failures
can be distinguished from a legitimate no-change refresh.

A fast refresh does not perform the full independent audit itself. Current completeness
can therefore be proven by either of two independent, fresh evidence paths:
1) the 25+11 support-office deep coverage report with exact-link totals, or
2) a fully successful 38-source stable-ID reconciliation (including both central portals),
   provided its central pagination proof, durable ID ledger, and jobs exact-link verification
   are all present and consistent.

Artifact existence alone is never treated as proof.
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
EVIDENCE_FRESH_HOURS = 30


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def jobs_payload():
    data = load_json(ROOT / "jobs.json", {})
    return data if isinstance(data, dict) else {}


def jobs_count():
    jobs = jobs_payload().get("jobs", [])
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


def freshness(value):
    dt = parse_timestamp(value)
    if dt is None:
        return None, False
    age = max(0.0, (datetime.now(KST) - dt).total_seconds() / 3600.0)
    return round(age, 2), age <= EVIDENCE_FRESH_HOURS


def support_coverage_evidence():
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
    age_hours, fresh = freshness(generated)
    return {
        "generatedAt": generated,
        "ageHours": age_hours,
        "freshWithinHours": EVIDENCE_FRESH_HOURS,
        "fresh": fresh,
        "gyeonggiComplete": gyeonggi_complete,
        "gyeonggiTotal": gyeonggi_total,
        "seoulComplete": seoul_complete,
        "seoulTotal": seoul_total,
        "warnings": len(warnings) if isinstance(warnings, list) else None,
        "exactLinks": exact_links,
        "complete": complete,
        "currentComplete": bool(complete and fresh),
    }


def reconciliation_evidence():
    """Return strict evidence for the independent all-38-source proof.

    The reconciliation report alone is insufficient: a failed workflow can preserve a report
    and ledger for diagnostics. Require all of the independently produced success invariants:
    38/38 sources, zero missing IDs, completed central pagination, a durable ledger generated in
    the same scan window, and the published jobs payload's exact support-link verification.
    """
    report = load_json(ROOT / "source_reconciliation_report.json", {})
    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    central = load_json(ROOT / "central_pagination_report.json", {})
    ledger = load_json(ROOT / "source_id_ledger.json", {})
    payload = jobs_payload()
    verification = payload.get("verification", {}) if isinstance(payload, dict) else {}
    exact = verification.get("supportExactLinks", {}) if isinstance(verification, dict) else {}

    generated = report.get("generatedAt", "") if isinstance(report, dict) else ""
    age_hours, fresh = freshness(generated)
    ledger_generated = ledger.get("generatedAt", "") if isinstance(ledger, dict) else ""
    ledger_age, ledger_fresh = freshness(ledger_generated)
    central_generated = central.get("generatedAt", "") if isinstance(central, dict) else ""
    central_age, central_fresh = freshness(central_generated)

    reconciled = int(summary.get("reconciledSources") or 0)
    total = int(summary.get("totalSources") or 0)
    missing_after = int(summary.get("missingAfter") or 0)
    exact_links = (
        isinstance(exact, dict)
        and exact.get("total") is not None
        and int(exact.get("unresolved") or 0) == 0
        and int(exact.get("resolved") or 0) == int(exact.get("total") or 0)
    )
    ledger_present = bool(isinstance(ledger, dict) and ledger.get("entries"))
    complete = (
        total == 38
        and reconciled == 38
        and missing_after == 0
        and bool(central.get("complete"))
        and ledger_present
        and exact_links
    )
    current = bool(complete and fresh and ledger_fresh and central_fresh)
    return {
        "generatedAt": generated,
        "ageHours": age_hours,
        "freshWithinHours": EVIDENCE_FRESH_HOURS,
        "fresh": fresh,
        "reconciledSources": reconciled,
        "totalSources": total,
        "missingAfter": missing_after,
        "centralPaginationComplete": bool(central.get("complete")),
        "centralGeneratedAt": central_generated,
        "centralAgeHours": central_age,
        "ledgerPresent": ledger_present,
        "ledgerGeneratedAt": ledger_generated,
        "ledgerAgeHours": ledger_age,
        "exactLinks": exact_links,
        "complete": complete,
        "currentComplete": current,
    }


def coverage_evidence():
    support = support_coverage_evidence()
    reconciliation = reconciliation_evidence()
    current = bool(support.get("currentComplete") or reconciliation.get("currentComplete"))
    return {
        # Preserve the old top-level fields for existing monitors while exposing both proof paths.
        **support,
        "currentComplete": current,
        "proof": "support-coverage" if support.get("currentComplete") else (
            "38-source-reconciliation" if reconciliation.get("currentComplete") else "none"
        ),
        "supportCoverage": support,
        "sourceReconciliation": reconciliation,
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

    evidence = coverage_evidence()
    now = datetime.now(KST).isoformat(timespec="seconds")
    entry = dict(old)
    entry.update({
        "state": args.state,
        "updatedAt": now,
        "jobsCount": jobs_count(),
        "artifacts": {
            "jobLedger": (ROOT / "job_ledger.json").exists(),
            "sourceIdLedger": (ROOT / "source_id_ledger.json").exists(),
            "collectionState": (ROOT / "collection_state.json").exists(),
            "coverageReport": (ROOT / "support_coverage_report.json").exists(),
            "sourceReconciliationReport": (ROOT / "source_reconciliation_report.json").exists(),
            "centralPaginationReport": (ROOT / "central_pagination_report.json").exists(),
            "missingRecoveryReport": (ROOT / "missing_recovery_report.json").exists(),
            "boardDiscoveryReport": (ROOT / "board_discovery_report.json").exists(),
        },
        "artifactGeneratedAt": {
            "coverageReport": generated_at(ROOT / "support_coverage_report.json"),
            "sourceReconciliationReport": generated_at(ROOT / "source_reconciliation_report.json"),
            "sourceIdLedger": generated_at(ROOT / "source_id_ledger.json"),
            "centralPaginationReport": generated_at(ROOT / "central_pagination_report.json"),
            "missingRecoveryReport": generated_at(ROOT / "missing_recovery_report.json"),
            "boardDiscoveryReport": generated_at(ROOT / "board_discovery_report.json"),
            "collectionState": generated_at(ROOT / "collection_state.json"),
        },
        "coverageEvidence": evidence,
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

    # Fast refreshes do not perform either independent proof themselves. Refuse publication
    # only when BOTH current proof paths are absent/stale/incomplete; the workflow rollback then
    # preserves the last known-good jobs.json.
    if (
        args.workflow == "fast"
        and args.stage == "publication-guard"
        and args.state == "running"
        and not evidence.get("currentComplete")
    ):
        raise SystemExit(
            "Refusing fast publication: no fresh complete support-coverage or 38-source reconciliation evidence"
        )


if __name__ == "__main__":
    main()
