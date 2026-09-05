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


def validate_source_evidence(report, expected_sources):
    """Verify every registry-counted source, not only reconciliation summary totals."""
    sources = report.get("sources") if isinstance(report, dict) else None
    if not isinstance(sources, list) or len(sources) != expected_sources:
        raise SystemExit(
            "Per-source reconciliation evidence does not match registry: "
            f"registry={expected_sources}, sourceRecords={len(sources) if isinstance(sources, list) else 'invalid'}"
        )
    if report.get("incompleteSources") or report.get("incompleteOffices"):
        raise SystemExit(
            "Reconciliation explicitly reports incomplete sources/offices: "
            f"sources={len(report.get('incompleteSources') or [])}, "
            f"offices={len(report.get('incompleteOffices') or [])}"
        )

    failures = []
    board_failures = 0
    total_access_errors = 0
    for src in sources:
        name = str(src.get("name") or "<unnamed>")
        access_errors = int(src.get("accessErrors") or 0)
        total_access_errors += access_errors
        reasons = []
        if src.get("reconciled") is not True:
            reasons.append("reconciled=false")
        if int(src.get("missingAfterCount") or 0) != 0:
            reasons.append(f"missingAfter={src.get('missingAfterCount')}")
        if src.get("coverageComplete") is not True:
            reasons.append("coverageComplete=false")
        if access_errors != 0:
            reasons.append(f"accessErrors={access_errors}")

        for board in src.get("boardHealth") or []:
            # A MirCMS menu alias is ignorable only after the reconciler proves that its stable-ID
            # set is a subset of a fully traversed representative and that the alias itself has no
            # access/pagination error. Do not reclassify that explicitly proven alias as a failure.
            if board.get("ignoredAsDuplicateMenuAlias") is True:
                continue
            if (
                board.get("accessError") is True
                or board.get("paginationRepeated") is True
                or board.get("coverageComplete") is not True
            ):
                board_failures += 1
                reasons.append("board-pagination/access-incomplete")
                break
        if reasons:
            failures.append({"source": name, "reasons": reasons})

    if failures:
        preview = "; ".join(
            f"{item['source']}({','.join(item['reasons'])})" for item in failures[:5]
        )
        raise SystemExit(
            f"Per-source reconciliation evidence is incomplete for {len(failures)} source(s): {preview}"
        )
    return {
        "sourceRecords": len(sources),
        "accessErrors": total_access_errors,
        "boardFailures": board_failures,
        "allCoverageComplete": True,
    }


def validate_current_ledger_population(ledger, expected_current_count):
    """Bind the append-only ledger's current-scan flags to the reconciliation summary.

    ``knownOfficialIdCount`` is intentionally historical and can exceed the current 90-day
    population. Publication safety therefore depends on entries explicitly marked as present in
    the latest official scan, not on the append-only total.
    """
    entries = ledger.get("entries") if isinstance(ledger, dict) else None
    if not isinstance(entries, dict):
        raise SystemExit("Stable-ID ledger has no entries mapping")

    present_ids = []
    malformed = []
    for sid, entry in entries.items():
        if not isinstance(entry, dict):
            malformed.append(str(sid))
            continue
        if entry.get("presentInLatestOfficialScan") is True:
            present_ids.append(str(sid))
            if str(entry.get("sourceIdentity") or "") != str(sid):
                malformed.append(str(sid))

    if malformed:
        raise SystemExit(
            f"Stable-ID ledger has malformed current entries: count={len(malformed)}, sample={malformed[:5]}"
        )
    if len(present_ids) != expected_current_count:
        raise SystemExit(
            "Stable-ID ledger current population disagrees with reconciliation summary: "
            f"ledgerPresent={len(present_ids)}, reconciliationOfficialIds={expected_current_count}"
        )
    return {
        "knownOfficialIds": int(ledger.get("knownOfficialIdCount") or len(entries)),
        "presentInLatestOfficialScan": len(present_ids),
    }


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

    official_id_count = int(summary.get("officialIdCount") or 0)
    if official_id_count <= 0:
        raise SystemExit(f"Reconciliation summary has invalid officialIdCount={summary.get('officialIdCount')}")
    ledger_evidence = validate_current_ledger_population(ledger, official_id_count)
    source_evidence = validate_source_evidence(report, expected_sources)
    evidence_age_hours = reconciliation_age_hours(report.get("generatedAt"))
    fast_shape = guard_fast_publication_shape()
    print(json.dumps({
        "populationPolicy": POLICY,
        "generatedAt": report.get("generatedAt"),
        "evidenceAgeHours": round(evidence_age_hours, 3),
        "maxEvidenceAgeHours": MAX_RECONCILIATION_AGE_HOURS,
        "officialIdCount": official_id_count,
        "ledgerEvidence": ledger_evidence,
        "expectedSources": expected_sources,
        "reconciledSources": reconciled_sources,
        "missingAfter": summary.get("missingAfter"),
        "sourceEvidence": source_evidence,
        "fastPublicationShape": fast_shape,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
