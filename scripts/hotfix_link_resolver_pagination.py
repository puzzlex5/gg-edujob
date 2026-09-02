#!/usr/bin/env python3
"""Regression guard for the support-office exact-link resolver.

Older versions deliberately forced fallback resolution to a fixed page count. That
can make a board look complete while silently missing an unresolved posting beyond
the cap. The resolver is now target-aware: canonical URLs are preserved, and any
fallback scan continues until its target is found or pagination is proven to have
ended/repeated. This script validates that invariant without rewriting the resolver.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/resolve_support_links.py"
s = p.read_text(encoding="utf-8")

required = (
    "def scan_gyeonggi_office(src, target_titles):",
    "seen_page_sigs = set()",
    "while True:",
    "if not remaining:",
    '"paginationRepeated": repeated',
    '"capHit": False',
    "def exact_gyeonggi_url(job):",
)
for marker in required:
    if marker not in s:
        raise SystemExit(f"Target-aware detail-link resolver marker missing: {marker}")

for forbidden in (
    "MAX_FALLBACK_PAGES",
    "for page in range(1, 9):",
    "for page in range(1, 501):",
):
    if forbidden in s:
        raise SystemExit(f"Fixed-page detail-link truncation regressed: {forbidden}")

print("Exact-link resolver pagination guard passed: target-aware scan has no fixed page cap")
