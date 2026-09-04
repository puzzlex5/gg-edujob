#!/usr/bin/env python3
"""Persist collector state and fail closed when current completeness evidence is absent.

A fast refresh can use either a fresh 25+11 deep coverage proof or a fresh successful
38-source stable-ID reconciliation. Historical proof is valid for publication only while
the current jobs.json still contains at least the support-office population proven by that
scan (and, for reconciliation, every official stable ID from the scan). This prevents a
later fast crawl from silently dropping postings while reusing a still-fresh proof.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from source_registry import official_source_count

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "collector_status.json"
KST = timezone(timedelta(hours=9))
EVIDENCE_FRESH_HOURS = 30
RECONCILIATION_SCAN_SKEW_HOURS = 3


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


def timestamp_skew_hours(a, b):
    da, db = parse_timestamp(a), parse_timestamp(b)
    if da is None or db is None:
        return None
    return abs((da - db).total_seconds()) / 3600.0


def stable_source_id(job):
    """Match reconcile_source_ids.py identities without importing crawler modules."""
    raw = str(job.get("url") or "")
    try:
        parsed = urlparse(raw)
        q = parse_qs(parsed.query)
    except Exception:
        parsed = urlparse("")
        q = {}

    if job.get("sourceType") == "통합게시판":
        pb = str((q.get("pbancSn") or [""])[0])
        if pb.isdigit():
            return f"goe-central:{pb}"
        rid = str((q.get("q_rcrtSn") or [""])[0])
        if rid.isdigit():
            return f"seoul-central:{rid}"

    province = str(job.get("province") or "")
    if province == "서울":
        seq = str((job.get("openParams") or {}).get("job_seq") or "")
        open_raw = str(job.get("openUrl") or raw)
        host = (urlparse(open_raw).hostname or "").lower()
        if not seq.isdigit():
            seq = str((q.get("job_seq") or [""])[0])
        return f"seoul:{host}:{seq}" if host and seq.isdigit() else ""

    if province == "경기":
        host = (parsed.hostname or "").lower()
        bbs = str(job.get("bbsId") or (q.get("bbsId") or [""])[0])
        ntt = str(job.get("nttSn") or (q.get("nttSn") or [""])[0])
        return f"mircms:{host}:{bbs}:{ntt}" if host and bbs.isdigit() and ntt.isdigit() else ""
    return ""


def current_stable_ids(payload):
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    return {sid for sid in (stable_source_id(j) for j in jobs if isinstance(j, dict)) if sid}


def current_support_link_evidence(payload):
    verification = payload.get("verification", {}) if isinstance(payload, dict) else {}
    exact = verification.get("supportExactLinks", {}) if isinstance(verification, dict) else {}
    total = int(exact.get("total") or 0) if isinstance(exact, dict) else 0
    resolved = int(exact.get("resolved") or 0) if isinstance(exact, dict) else 0
    unresolved = int(exact.get("unresolved") or 0) if isinstance(exact, dict) else 0
    return {
        "total": total,
        "resolved": resolved,
        "unresolved": unresolved,
        "exact": bool(total > 0 and unresolved == 0 and resolved == total),
    }


def support_coverage_evidence():
    report = load_json(ROOT / "support_coverage_report.json", {})
    summary = report.get("supportCompleteness", {}) if isinstance(report, dict) else {}
    links = report.get("supportLinkResolution", {}) if isinstance(report, dict) else {}
    warnings = summary.get("warnings", []) if isinstance(summary, dict) else []
    gg_c = summary.get("gyeonggiComplete")
    gg_t = summary.get("gyeonggiTotal")
    se_c = summary.get("seoulComplete")
    se_t = summary.get("seoulTotal")
    reference_gg = int(links.get("gyeonggiTotal") or 0) if isinstance(links, dict) else 0
    reference_se = int(links.get("seoulTotal") or 0) if isinstance(links, dict) else 0
    reference_total = reference_gg + reference_se
    exact_links = bool(
        isinstance(links, dict)
        and links.get("gyeonggiTotal") is not None
        and links.get("seoulTotal") is not None
        and links.get("gyeonggiExact") == links.get("gyeonggiTotal")
        and links.get("seoulExact") == links.get("seoulTotal")
    )
    complete = gg_c == 25 and gg_t == 25 and se_c == 11 and se_t == 11 and not warnings and exact_links
    generated = report.get("generatedAt", "") if isinstance(report, dict) else ""
    age, fresh = freshness(generated)

    current_links = current_support_link_evidence(jobs_payload())
    dataset_bound = bool(
        reference_total > 0
        and current_links["exact"]
        and current_links["total"] >= reference_total
    )
    return {
        "generatedAt": generated,
        "ageHours": age,
        "freshWithinHours": EVIDENCE_FRESH_HOURS,
        "fresh": fresh,
        "gyeonggiComplete": gg_c,
        "gyeonggiTotal": gg_t,
        "seoulComplete": se_c,
        "seoulTotal": se_t,
        "warnings": len(warnings) if isinstance(warnings, list) else None,
        "exactLinks": exact_links,
        "complete": complete,
        "referenceSupportPostingCount": reference_total,
        "currentSupportPostingCount": current_links["total"],
        "currentSupportExactLinks": current_links["exact"],
        "datasetBound": dataset_bound,
        "currentComplete": bool(complete and fresh and dataset_bound),
    }


def reconciliation_evidence():
    report = load_json(ROOT / "source_reconciliation_report.json", {})
    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    expected_sources = official_source_count()
    central = load_json(ROOT / "central_pagination_report.json", {})
    ledger = load_json(ROOT / "source_id_ledger.json", {})
    payload = jobs_payload()
    verification = payload.get("verification", {}) if isinstance(payload, dict) else {}
    exact = verification.get("supportExactLinks", {}) if isinstance(verification, dict) else {}

    generated = report.get("generatedAt", "") if isinstance(report, dict) else ""
    report_age, report_fresh = freshness(generated)
    ledger_generated = ledger.get("generatedAt", "") if isinstance(ledger, dict) else ""
    ledger_age, ledger_fresh = freshness(ledger_generated)
    central_generated = central.get("generatedAt", "") if isinstance(central, dict) else ""
    central_age, central_fresh = freshness(central_generated)

    report_ledger_skew = timestamp_skew_hours(generated, ledger_generated)
    report_central_skew = timestamp_skew_hours(generated, central_generated)
    same_scan_window = bool(
        report_ledger_skew is not None
        and report_ledger_skew <= 0.05
        and report_central_skew is not None
        and report_central_skew <= RECONCILIATION_SCAN_SKEW_HOURS
    )

    reconciled = int(summary.get("reconciledSources") or 0)
    total = int(summary.get("totalSources") or 0)
    missing_after = int(summary.get("missingAfter") or 0)
    exact_links = bool(
        isinstance(exact, dict)
        and exact.get("total") is not None
        and int(exact.get("unresolved") or 0) == 0
        and int(exact.get("resolved") or 0) == int(exact.get("total") or 0)
    )

    entries = ledger.get("entries", {}) if isinstance(ledger, dict) else {}
    required_ids = {
        sid for sid, entry in entries.items()
        if isinstance(entry, dict) and entry.get("presentInLatestOfficialScan") is True
    }
    current_ids = current_stable_ids(payload)
    missing_from_current = sorted(required_ids - current_ids)
    dataset_bound = bool(required_ids and not missing_from_current)

    complete = bool(
        total == expected_sources
        and reconciled == expected_sources
        and missing_after == 0
        and central.get("complete") is True
        and required_ids
        and exact_links
        and same_scan_window
        and dataset_bound
    )
    current = bool(complete and report_fresh and ledger_fresh and central_fresh)
    return {
        "generatedAt": generated,
        "ageHours": report_age,
        "freshWithinHours": EVIDENCE_FRESH_HOURS,
        "fresh": report_fresh,
        "reconciledSources": reconciled,
        "totalSources": total,
        "missingAfter": missing_after,
        "centralPaginationComplete": central.get("complete") is True,
        "centralGeneratedAt": central_generated,
        "centralAgeHours": central_age,
        "ledgerPresent": bool(required_ids),
        "ledgerGeneratedAt": ledger_generated,
        "ledgerAgeHours": ledger_age,
        "sameScanWindow": same_scan_window,
        "reportLedgerSkewHours": round(report_ledger_skew, 3) if report_ledger_skew is not None else None,
        "reportCentralSkewHours": round(report_central_skew, 3) if report_central_skew is not None else None,
        "requiredOfficialIds": len(required_ids),
        "currentMatchedOfficialIds": len(required_ids & current_ids),
        "missingOfficialIdsFromCurrentDataset": len(missing_from_current),
        "missingOfficialIdExamples": missing_from_current[:20],
        "datasetBound": dataset_bound,
        "exactLinks": exact_links,
        "complete": complete,
        "currentComplete": current,
    }


def coverage_evidence():
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

    if (
        args.workflow == "fast"
        and args.stage == "publication-guard"
        and args.state == "running"
        and not evidence.get("currentComplete")
    ):
        raise SystemExit(
            "Refusing fast publication: no fresh completeness proof bound to the current dataset"
        )


if __name__ == "__main__":
    main()
