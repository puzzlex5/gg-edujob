#!/usr/bin/env python3
"""Fail closed unless reconciliation artifacts were produced by the current population policy."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = "stable-id-38-v2-gyeonggi-central-90d"


def load(name):
    path = ROOT / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Unreadable reconciliation artifact {name}: {type(exc).__name__}")


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

    if int(summary.get("totalSources") or 0) != 38 or int(summary.get("reconciledSources") or 0) != 38:
        raise SystemExit("Current-policy reconciliation is not 38/38")
    if int(summary.get("missingAfter") or 0) != 0:
        raise SystemExit(f"Current-policy reconciliation still has missingAfter={summary.get('missingAfter')}")
    if report.get("generatedAt") != ledger.get("generatedAt"):
        raise SystemExit("Reconciliation report and stable-ID ledger are not from the same scan")

    print(json.dumps({
        "populationPolicy": POLICY,
        "generatedAt": report.get("generatedAt"),
        "officialIdCount": summary.get("officialIdCount"),
        "reconciledSources": summary.get("reconciledSources"),
        "missingAfter": summary.get("missingAfter"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
