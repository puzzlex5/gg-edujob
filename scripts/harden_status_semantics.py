#!/usr/bin/env python3
"""Harden fast-publication status semantics before every fast crawl.

The hardener is intentionally idempotent: if the current collector-status implementation
already contains the authoritative reconciliation semantics, it validates those invariants
and exits successfully instead of trying to rediscover an obsolete source-text anchor.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/update_collector_status.py"
s = p.read_text(encoding="utf-8")

# Current main already has the stronger fail-closed coverage contract. Treat that exact
# semantic implementation as an already-hardened state; do not depend on an obsolete
# literal block that may have been refactored while preserving the same invariants.
current_marker = "# Do not let fresh 38-source ID reconciliation mask stale/incomplete 25+11 support-office coverage."
current_invariants = (
    "current = bool(support.get(\"currentComplete\") and reconciliation.get(\"currentComplete\"))",
    'proof = "support-coverage+38-source-reconciliation" if current else "none"',
    '"coverageEvidence": evidence',
    'if args.workflow == "fast" and args.stage == "publication-guard"',
    'not evidence.get("currentComplete")',
)

if current_marker in s and all(marker in s for marker in current_invariants):
    print("Status semantics already hardened; validation passed (no-op)")
    raise SystemExit(0)

raise SystemExit(
    "Cannot locate a recognized update_collector_status coverage contract; refusing to patch blindly"
)
