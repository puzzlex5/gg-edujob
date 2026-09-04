#!/usr/bin/env python3
"""Fail closed unless reconciliation artifacts and fast publication shape are trustworthy."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from source_registry import official_source_count

ROOT = Path(__file__).resolve().parents[1]
POLICY = "stable-id-38-v2-gyeonggi-central-90d"
FAST_BASELINE = Path("/tmp/jobs_previous.json")
MAX_FAST_DROP_RATIO = 0.10
MAX_FAST_GROWTH_RATIO = 0.10
# Reconciliation runs hourly, but all data workflows share one serialization group. Allow
# queue/runtime jitter while refusing the previous effectively day-old (30h) proof window.
MAX_RECONCILIATION_AGE_HOURS = 4.0
MAX_FUTURE_SKEW_MINUTES = 15
KST = timezone(timedelta(hours=9))


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


def reconciliation_age_hours(generated_at):
    """Return evidence age and reject malformed/future timestamps.

    Reconciliation currently persists ``YYYY-mm-dd HH:MM:SS KST``. Keeping this parser here makes
    the publication guard independent of collector_status.json's looser historical freshness field.
    """
    if not isinstance(generated_at, str) or not generated_at.strip():
        raise SystemExit("Reconciliation evidence has no generatedAt timestamp")
    raw = generated_at.strip()
    try:
        if raw.endswith(" KST"):
            generated = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S KST").replace(tzinfo=KST)
        else:
            generated = datetime.fromisoformat(raw)
            if generated.tzinfo is None:
                raise ValueError("timezone required")
    except ValueError as exc:
        raise SystemExit(f"Unparseable reconciliation generatedAt={generated_at!r}: {exc}")

    now = datetime.now(timezone.utc)
    age = (now - generated.astimezone(timezone.utc)).total_seconds() / 3600.0
    if age < -(MAX_FUTURE_SKEW_MINUTES / 60.0):
        raise SystemExit(f"Reconciliation evidence timestamp is unexpectedly in the future: ageHours={age:.2f}")
    if age > MAX_RECONCILIATION_AGE_HOURS:
        raise SystemExit(
            f"Official reconciliation evidence is stale: ageHours={age:.2f}, "
            f"maxHours={MAX_RECONCILIATION_AGE_HOURS:.1f}; keep last-known-good publication"
        )
    return max(age, 0.0)


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
    report = load("source_reconciliation_report.json")
    ledger = load("source_id_ledger.json")
    summary = report.get("summary", {}) if isinstance(report, dict) else {}

    report_policy = report.get("populationPolicy") or summary.get("populationPolicy")
    ledger_policy = ledger.get("populationPolicy") if isinstance(ledger, dict) else None
    if report_policy != POLICY or ledger_policy != POLICY:
        raise SystemExit(
            f"Reconciliation evidence predates current 90-day policy: report={report_policy!r}, ledger={ledger_policy!r}"
        )

    expected_sources = official_source_count()
    total_sources = int(summary.get("totalSources") or 0)
    reconciled_sources = int(summary.get("reconciledSources") or 0)
    if total_sources != expected_sources or reconciled_sources != expected_sources:
        raise SystemExit(
            "Current-policy reconciliation source count does not match registry: "
            f"registry={expected_sources}, reconciled={reconciled_sources}, total={total_sources}"
        )
    if int(summary.get("missingAfter") or 0) != 0:
        raise SystemExit(f"Current-policy reconciliation still has missingAfter={summary.get('missingAfter')}")
    if report.get("generatedAt") != ledger.get("generatedAt"):
        raise SystemExit("Reconciliation report and stable-ID ledger are not from the same scan")

    evidence_age_hours = reconciliation_age_hours(report.get("generatedAt"))
    fast_shape = guard_fast_publication_shape()
    print(json.dumps({
        "populationPolicy": POLICY,
        "generatedAt": report.get("generatedAt"),
        "evidenceAgeHours": round(evidence_age_hours, 3),
        "maxEvidenceAgeHours": MAX_RECONCILIATION_AGE_HOURS,
        "officialIdCount": summary.get("officialIdCount"),
        "expectedSources": expected_sources,
        "reconciledSources": reconciled_sources,
        "missingAfter": summary.get("missingAfter"),
        "fastPublicationShape": fast_shape,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
