#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import preserve_reconciliation_candidate as preserve
import scan_identity
import stamp_reconciliation_scan as stamp
import validate_reconciliation_policy as validator


def expect_system_exit(fn, contains: str) -> None:
    try:
        fn()
    except SystemExit as exc:
        text = str(exc)
        assert contains in text, (contains, text)
    else:
        raise AssertionError(f"expected SystemExit containing {contains!r}")


def ledger(ids, scan_id="S-1"):
    return {
        "scanId": scan_id,
        "entries": {
            sid: {
                "sourceIdentity": sid,
                "source": "fixture",
                "province": "서울",
                "presentInLatestOfficialScan": True,
            }
            for sid in ids
        },
    }


def test_scan_identity() -> None:
    old = os.environ.pop(scan_identity.ENV_NAME, None)
    try:
        assert scan_identity.current_scan_id() is None
        try:
            scan_identity.require_scan_id()
        except RuntimeError as exc:
            assert scan_identity.ENV_NAME in str(exc)
        else:
            raise AssertionError("require_scan_id must fail closed when unset")
        os.environ[scan_identity.ENV_NAME] = "20260905T160000Z-12345-1"
        assert scan_identity.require_scan_id() == "20260905T160000Z-12345-1"
    finally:
        if old is None:
            os.environ.pop(scan_identity.ENV_NAME, None)
        else:
            os.environ[scan_identity.ENV_NAME] = old


def test_signed_delta() -> None:
    canonical = ledger({"a", "b", "c", "d", "e"})
    candidate = ledger({"a", "b", "x", "y"}, "S-2")
    delta = preserve.build_delta(canonical, candidate)
    assert len(delta["removedSinceCanonical"]) == 3
    assert len(delta["addedSinceCanonical"]) == 2
    assert delta["candidateCount"] - delta["canonicalCount"] == 2 - 3
    assert delta["deltaConsistent"] is True
    assert 2 + 3 != abs(delta["candidateCount"] - delta["canonicalCount"])


def test_proof_window_gate() -> None:
    original = validator.load_optional
    try:
        validator.load_optional = lambda name: {
            "central_pagination_report.json": {"scanId": "SAME"},
            "official_id_restore_report.json": {"summary": {}},
        }.get(name, {})
        report = {"scanId": "SAME"}
        summary = {"scanId": "SAME"}
        stable = {"scanId": "SAME"}
        assert validator.guard_proof_window(report, stable, summary) == "SAME"

        validator.load_optional = lambda name: {
            "central_pagination_report.json": {"scanId": "OTHER"},
            "official_id_restore_report.json": {"summary": {}},
        }.get(name, {})
        expect_system_exit(
            lambda: validator.guard_proof_window(report, stable, summary),
            "proof-window-mismatch",
        )

        validator.load_optional = lambda name: {
            "central_pagination_report.json": {"scanId": "SAME"},
            "official_id_restore_report.json": {"summary": {"ledgerScanId": "FORGED"}},
        }.get(name, {})
        expect_system_exit(
            lambda: validator.guard_proof_window(report, stable, summary),
            "proof-window-mismatch",
        )
    finally:
        validator.load_optional = original


def test_stamp_reconciliation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generated = "2026-09-06 01:00:00 KST"
        (root / "jobs.json").write_text(json.dumps({"sourceReconciliation": {"generatedAt": generated}}), encoding="utf-8")
        (root / "source_id_ledger.json").write_text(json.dumps({"generatedAt": generated}), encoding="utf-8")
        (root / "source_reconciliation_report.json").write_text(json.dumps({"generatedAt": generated, "summary": {"generatedAt": generated}}), encoding="utf-8")
        old_root = stamp.ROOT
        try:
            stamp.ROOT = root
            stamp.stamp_reconciliation("SCAN-X")
        finally:
            stamp.ROOT = old_root
        jobs = json.loads((root / "jobs.json").read_text(encoding="utf-8"))
        ledger_data = json.loads((root / "source_id_ledger.json").read_text(encoding="utf-8"))
        report = json.loads((root / "source_reconciliation_report.json").read_text(encoding="utf-8"))
        assert jobs["sourceReconciliation"]["scanId"] == "SCAN-X"
        assert ledger_data["scanId"] == "SCAN-X"
        assert report["scanId"] == "SCAN-X"
        assert report["summary"]["scanId"] == "SCAN-X"


def main() -> None:
    tests = [
        test_scan_identity,
        test_signed_delta,
        test_proof_window_gate,
        test_stamp_reconciliation,
    ]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"passed={len(tests)} failed=0")


if __name__ == "__main__":
    main()
