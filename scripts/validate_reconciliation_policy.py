#!/usr/bin/env python3
"""Fail closed unless reconciliation artifacts and fast publication shape are trustworthy."""
import json
from pathlib import Path

from source_registry import official_source_count

ROOT = Path(__file__).resolve().parents[1]
POLICY = "stable-id-38-v2-gyeonggi-central-90d"
FAST_BASELINE = Path("/tmp/jobs_previous.json")
MAX_FAST_DROP_RATIO = 0.10
MAX_FAST_GROWTH_RATIO = 0.10


def load(name):
    path = ROOT / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Unreadable reconciliation artifact {name}: {type(exc).__name__}")


def load_path(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Unreadable publication baseline {path}: {type(exc).__name__}")


def job_count(data):
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return 0
    return len(data["jobs"])


def guard_fast_publication_shape():
    """Reject unexplained large fast-run population changes and keep the last published baseline.

    The hourly/fast path is incremental. Large population corrections belong in the independently
    reconciled deep-audit/recovery path, not in a fast run where a parser/network regression can
    masquerade as legitimate retention cleanup.
    """
    if not FAST_BASELINE.exists():
        return None

    previous = load_path(FAST_BASELINE)
    current = load("jobs.json")
    before = job_count(previous)
    after = job_count(current)
    if before < 100 or after < 100:
        raise SystemExit(f"Fast publication population is invalid: previous={before}, current={after}")

    low = int(before * (1.0 - MAX_FAST_DROP_RATIO))
    high = int(before * (1.0 + MAX_FAST_GROWTH_RATIO))
    if after < low:
        drop_pct = (before - after) * 100.0 / before
        raise SystemExit(
            f"Refusing unexplained fast population drop: {before}->{after} ({drop_pct:.1f}%); "
            "keep previous jobs.json and require deep-audit/recovery evidence for bulk cleanup"
        )
    if after > high:
        growth_pct = (after - before) * 100.0 / before
        raise SystemExit(
            f"Refusing unexplained fast population growth: {before}->{after} (+{growth_pct:.1f}%); "
            "keep previous jobs.json and investigate parser/pagination overcollection"
        )
    return {"previousJobs": before, "currentJobs": after, "allowedRange": [low, high]}


def main():
    expected_sources = official_source_count()
    report = load("source_reconciliation_report.json")
    ledger = load("source_id_ledger.json")
    summary = report.get("summary", {}) if isinstance(report, dict) else {}

    report_policy = report.get("populationPolicy") or summary.get("populationPolicy")
    ledger_policy = ledger.get("populationPolicy") if isinstance(ledger, dict) else None
    if report_policy != POLICY or ledger_policy != POLICY:
        raise SystemExit(
            f"Reconciliation evidence predates current 90-day policy: report={report_policy!r}, ledger={ledger_policy!r}"
        )

    if (
        int(summary.get("totalSources") or 0) != expected_sources
        or int(summary.get("reconciledSources") or 0) != expected_sources
    ):
        raise SystemExit(
            f"Current-policy reconciliation is not {expected_sources}/{expected_sources}"
        )
    if int(summary.get("missingAfter") or 0) != 0:
        raise SystemExit(f"Current-policy reconciliation still has missingAfter={summary.get('missingAfter')}")
    if report.get("generatedAt") != ledger.get("generatedAt"):
        raise SystemExit("Reconciliation report and stable-ID ledger are not from the same scan")

    fast_shape = guard_fast_publication_shape()
    print(json.dumps({
        "populationPolicy": POLICY,
        "generatedAt": report.get("generatedAt"),
        "officialIdCount": summary.get("officialIdCount"),
        "reconciledSources": summary.get("reconciledSources"),
        "missingAfter": summary.get("missingAfter"),
        "fastPublicationShape": fast_shape,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
