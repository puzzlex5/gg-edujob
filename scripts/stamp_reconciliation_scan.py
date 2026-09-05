#!/usr/bin/env python3
"""Bind freshly generated reconciliation proof artifacts to one workflow scan ID.

This script never mints IDs. It is called immediately after proof writers and
fails closed if the reconciliation writer outputs are not internally timestamp-aligned.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scan_identity import require_scan_id

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"cannot read {path.name}: {type(exc).__name__}")
    if not isinstance(data, dict):
        raise SystemExit(f"{path.name} is not a JSON object")
    return data


def write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def stamp_central(scan_id: str) -> None:
    path = ROOT / "central_pagination_report.json"
    report = load(path)
    if not report.get("generatedAt"):
        raise SystemExit("central pagination proof has no generatedAt")
    report["scanId"] = scan_id
    write(path, report)


def stamp_reconciliation(scan_id: str) -> None:
    jobs_path = ROOT / "jobs.json"
    ledger_path = ROOT / "source_id_ledger.json"
    report_path = ROOT / "source_reconciliation_report.json"
    jobs = load(jobs_path)
    ledger = load(ledger_path)
    report = load(report_path)
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    payload_summary = jobs.get("sourceReconciliation") if isinstance(jobs.get("sourceReconciliation"), dict) else {}

    generated = {
        "report": report.get("generatedAt"),
        "ledger": ledger.get("generatedAt"),
        "summary": summary.get("generatedAt"),
        "jobs": payload_summary.get("generatedAt"),
    }
    if not all(generated.values()) or len(set(generated.values())) != 1:
        raise SystemExit(f"refusing to bind misaligned reconciliation outputs: {generated}")

    ledger["scanId"] = scan_id
    report["scanId"] = scan_id
    summary["scanId"] = scan_id
    payload_summary["scanId"] = scan_id
    report["summary"] = summary
    jobs["sourceReconciliation"] = payload_summary
    write(ledger_path, ledger)
    write(report_path, report)
    write(jobs_path, jobs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--central", action="store_true")
    parser.add_argument("--reconciliation", action="store_true")
    args = parser.parse_args()
    if not args.central and not args.reconciliation:
        raise SystemExit("choose --central and/or --reconciliation")
    scan_id = require_scan_id()
    if args.central:
        stamp_central(scan_id)
    if args.reconciliation:
        stamp_reconciliation(scan_id)
    print(json.dumps({"scanId": scan_id, "central": args.central, "reconciliation": args.reconciliation}))


if __name__ == "__main__":
    main()
