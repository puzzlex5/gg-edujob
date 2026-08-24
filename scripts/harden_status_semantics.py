#!/usr/bin/env python3
"""Harden fast-publication status semantics before every fast crawl.

Two invariants are enforced:
1) A fresh 38-source reconciliation is authoritative. If it says the current jobs.json is
   missing any official stable IDs (or the 38-source proof itself is incomplete), a legacy
   25+11 support-coverage proof must not mask that loss.
2) Once a fast run has reached complete/success, a follow-on git sync/commit failure must not
   overwrite that success as a collector failure when there is no verification diagnostic and
   current completeness evidence is still valid.

The patch is idempotent and intentionally narrow so existing verification logic remains intact.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/update_collector_status.py"
s = p.read_text(encoding="utf-8")

old_cov = '''def coverage_evidence():
    support = support_coverage_evidence()
    reconciliation = reconciliation_evidence()
    current = bool(support.get("currentComplete") or reconciliation.get("currentComplete"))
    proof = "support-coverage" if support.get("currentComplete") else (
        "38-source-reconciliation" if reconciliation.get("currentComplete") else "none"
    )
    return {
        **support,
        "currentComplete": current,
        "proof": proof,
        "supportCoverage": support,
        "sourceReconciliation": reconciliation,
    }
'''

new_cov = '''def coverage_evidence():
    support = support_coverage_evidence()
    reconciliation = reconciliation_evidence()

    # Once a fresh stable-ID ledger/reconciliation exists, it is the stronger and authoritative
    # completeness proof. Do not allow a support-count proof to hide missing official IDs.
    reconciliation_authoritative = bool(
        reconciliation.get("fresh")
        and reconciliation.get("ledgerPresent")
        and int(reconciliation.get("totalSources") or 0) > 0
    )
    if reconciliation_authoritative:
        current = bool(reconciliation.get("currentComplete"))
        proof = "38-source-reconciliation" if current else "none"
    else:
        current = bool(support.get("currentComplete") or reconciliation.get("currentComplete"))
        proof = "support-coverage" if support.get("currentComplete") else (
            "38-source-reconciliation" if reconciliation.get("currentComplete") else "none"
        )
    return {
        **support,
        "currentComplete": current,
        "proof": proof,
        "reconciliationAuthoritative": reconciliation_authoritative,
        "supportCoverage": support,
        "sourceReconciliation": reconciliation,
    }
'''

if new_cov not in s:
    if old_cov not in s:
        raise SystemExit("Cannot locate coverage_evidence block")
    s = s.replace(old_cov, new_cov, 1)

old_failed = '''    elif args.state == "failed":
        entry["lastFailureAt"] = now

    all_status[args.workflow] = entry
'''
new_failed = '''    elif args.state == "failed":
        # A collector that already reached complete/success can still hit a transient git
        # synchronization failure afterward. That is a publication-sync problem, not a data
        # collection failure. Preserve success when there is no verification diagnostic and the
        # current dataset still has authoritative completeness evidence.
        previous_success = parse_timestamp(old.get("lastSuccessAt"))
        seconds_since_success = (
            (datetime.now(KST) - previous_success).total_seconds()
            if previous_success is not None else None
        )
        false_post_success_failure = bool(
            args.workflow == "fast"
            and old.get("stage") == "complete"
            and old.get("state") == "success"
            and seconds_since_success is not None
            and 0 <= seconds_since_success <= 120
            and not (ROOT / "verification_failure_report.json").exists()
            and evidence.get("currentComplete")
        )
        if false_post_success_failure:
            entry["state"] = "success"
            entry["stage"] = "complete"
            entry["updatedAt"] = old.get("updatedAt") or now
            entry["lastSuccessAt"] = old.get("lastSuccessAt")
            entry["postSuccessSyncWarningAt"] = now
        else:
            entry["lastFailureAt"] = now

    all_status[args.workflow] = entry
'''

if new_failed not in s:
    if old_failed not in s:
        raise SystemExit("Cannot locate failed-state block")
    s = s.replace(old_failed, new_failed, 1)

required = (
    '"reconciliationAuthoritative": reconciliation_authoritative',
    'false_post_success_failure = bool(',
    'postSuccessSyncWarningAt',
)
for marker in required:
    if marker not in s:
        raise SystemExit(f"Status hardening missing: {marker}")

p.write_text(s, encoding="utf-8")
print("Collector official-ID and post-success status semantics hardened")
