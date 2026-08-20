#!/usr/bin/env python3
"""Compatibility guard for the support-office exact-link resolver.

Older versions deliberately forced the fallback resolver to eight pages. That made
80-row board statistics look complete and could miss a legacy unresolved posting
outside the first eight pages. The resolver is now target-aware: canonical URLs are
kept without rescanning, while genuinely unresolved titles paginate until found or
a proven board end/repeat is reached. This script now only prevents regression.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/resolve_support_links.py"
s = p.read_text(encoding="utf-8")

required = (
    "MAX_FALLBACK_PAGES = 60",
    "def scan_gyeonggi_office(src, target_titles):",
    "seen_page_sigs = set()",
    "if not remaining:",
    '"paginationRepeated": repeated',
    '"capHit": cap_hit',
    "def exact_gyeonggi_url(job):",
)
for marker in required:
    if marker not in s:
        raise SystemExit(f"Target-aware detail-link resolver marker missing: {marker}")

for forbidden in (
    "for page in range(1, 9):",
    "for page in range(1, 501):",
):
    if forbidden in s:
        raise SystemExit(f"Fixed-page detail-link truncation regressed: {forbidden}")

print("Exact-link resolver pagination guard passed: no fixed 8-page truncation")
