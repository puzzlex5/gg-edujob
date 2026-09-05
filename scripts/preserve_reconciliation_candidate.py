#!/usr/bin/env python3
"""Preserve rejected reconciliation proof without mutating canonical LKG artifacts."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def present_ids(ledger: dict) -> set[str]:
    entries = ledger.get("entries") if isinstance(ledger, dict) else None
    if not isinstance(entries, dict):
        return set()
    return {
        str(sid)
        for sid, entry in entries.items()
        if isinstance(entry, dict) and entry.get("presentInLatestOfficialScan") is True
    }


def detail(sid: str, ledger: dict) -> dict:
    entry = (ledger.get("entries") or {}).get(sid, {}) if isinstance(ledger, dict) else {}
    if not isinstance(entry, dict):
        entry = {}
    return {
        "sourceIdentity": sid,
        "source": entry.get("source"),
        "province": entry.get("province"),
        "title": entry.get("title"),
        "registered": entry.get("registered"),
        "firstSeen": entry.get("firstSeen"),
        "lastSeen": entry.get("lastSeen"),
    }


def build_delta(canonical: dict, candidate: dict) -> dict:
    canonical_ids = present_ids(canonical)
    candidate_ids = present_ids(candidate)
    removed = sorted(canonical_ids - candidate_ids)
    added = sorted(candidate_ids - canonical_ids)
    canonical_count = len(canonical_ids)
    candidate_count = len(candidate_ids)
    return {
        "canonicalScanId": canonical.get("scanId"),
        "candidateScanId": candidate.get("scanId"),
        "canonicalCount": canonical_count,
        "candidateCount": candidate_count,
        "removedSinceCanonical": [detail(sid, canonical) for sid in removed],
        "addedSinceCanonical": [detail(sid, candidate) for sid in added],
        "deltaConsistent": (candidate_count - canonical_count) == (len(added) - len(removed)),
        "symmetricDifferenceCount": len(added) + len(removed),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostics-dir", default="/tmp/source-id-diagnostics")
    parser.add_argument("--canonical-ledger", default=str(ROOT / "source_id_ledger.json"))
    parser.add_argument("--candidate-ledger-out", default=str(ROOT / "source_id_ledger.candidate.json"))
    parser.add_argument("--failure-report", default=str(ROOT / "reconciliation_failure_report.json"))
    args = parser.parse_args()

    diag = Path(args.diagnostics_dir)
    canonical_path = Path(args.canonical_ledger)
    candidate_source = diag / "source_id_ledger.json"
    candidate_out = Path(args.candidate_ledger_out)
    failure_path = Path(args.failure_report)

    canonical = load_json(canonical_path)
    candidate = load_json(candidate_source)
    candidate_available = bool(candidate)

    if candidate_available:
        candidate_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate_source, candidate_out)

    recon = load_json(diag / "source_reconciliation_report.json")
    central = load_json(diag / "central_pagination_report.json")
    gg90 = load_json(diag / "gyeonggi_central_90d_report.json")
    restore = load_json(diag / "official_id_restore_report.json")
    delta = build_delta(canonical, candidate) if candidate_available else {
        "canonicalScanId": canonical.get("scanId"),
        "candidateScanId": None,
        "canonicalCount": len(present_ids(canonical)),
        "candidateCount": None,
        "removedSinceCanonical": [],
        "addedSinceCanonical": [],
        "deltaConsistent": None,
        "symmetricDifferenceCount": None,
    }

    report = {
        "generatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "policy": "Rejected reconciliation diagnostics are isolated from canonical published proof files; candidate stable-ID deltas are retained for audit.",
        "candidateAvailable": candidate_available,
        "canonicalScanId": delta["canonicalScanId"],
        "candidateScanId": delta["candidateScanId"],
        "canonicalOfficialIdCount": delta["canonicalCount"],
        "candidateOfficialIdCount": delta["candidateCount"],
        "removedSinceCanonical": delta["removedSinceCanonical"],
        "addedSinceCanonical": delta["addedSinceCanonical"],
        "deltaConsistent": delta["deltaConsistent"],
        "symmetricDifferenceCount": delta["symmetricDifferenceCount"],
        "candidate": {
            "reconciliation": recon.get("summary", {}),
            "centralPagination": {
                "scanId": central.get("scanId"),
                "generatedAt": central.get("generatedAt"),
                "complete": central.get("complete"),
                "sources": central.get("sources", []),
            },
            "gyeonggiCentral90d": {
                "generatedAt": gg90.get("generatedAt"),
                "stableIdCount": gg90.get("stableIdCount"),
                "complete": gg90.get("complete"),
            },
            "finalRestore": restore.get("summary", {}),
            "sourceIdLedger": {
                "scanId": candidate.get("scanId") if candidate_available else None,
                "generatedAt": candidate.get("generatedAt") if candidate_available else None,
                "officialIdCount": len(present_ids(candidate)) if candidate_available else None,
            },
        },
    }
    failure_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "candidateAvailable": candidate_available,
        "canonicalScanId": report["canonicalScanId"],
        "candidateScanId": report["candidateScanId"],
        "removed": len(report["removedSinceCanonical"]),
        "added": len(report["addedSinceCanonical"]),
        "deltaConsistent": report["deltaConsistent"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
