#!/usr/bin/env python3
"""Convert low legacy page caps into emergency-only safety ceilings.

Collectors must normally stop because the official board is exhausted, repeats, or the
retention window is safely crossed—not because an arbitrary small page number was reached.
A finite 500-page ceiling remains only as protection against a broken pagination endpoint.
The completeness crawler treats hitting that ceiling as incomplete/unhealthy.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch(path, replacements, required=()):
    p = ROOT / path
    s = p.read_text(encoding="utf-8")
    changed = 0
    for old, new in replacements:
        if old in s:
            s = s.replace(old, new)
            changed += 1
        elif new not in s:
            raise SystemExit(f"Unexpected pagination marker in {path}: {old}")
    for marker in required:
        if marker not in s:
            raise SystemExit(f"Required safety marker missing in {path}: {marker}")
    p.write_text(s, encoding="utf-8")
    return changed


changed = 0
changed += patch(
    "scripts/complete_support_coverage.py",
    [("MAX_PAGES = 80", "MAX_PAGES = 500  # emergency infinite-loop ceiling; reaching it is NOT complete")],
    required=("pages < MAX_PAGES",),
)
# The primary collectors already stop on empty/repeated/no-new rows. Raise only their low
# artificial ceilings so those content-based stop conditions, rather than page number, govern.
changed += patch(
    "scripts/scrape_jobs.py",
    [
        ("for page in range(1, 35):", "for page in range(1, 501):"),
        ("for page in range(1, 8):", "for page in range(1, 501):"),
        ("for page in range(1, 60):", "for page in range(1, 501):"),
        ("for page in range(1, 10):", "for page in range(1, 501):"),
    ],
)
# This repair pass is followed by the stricter completeness crawler. Its low 7-page ceiling
# must not truncate a busy board before the complete pass can reconcile it.
changed += patch(
    "scripts/repair_gyeonggi_support.py",
    [("for page in range(1, 8):", "for page in range(1, 501):")],
)

# Guard against accidental reintroduction of the known low caps.
for rel, bad in {
    "scripts/complete_support_coverage.py": ("MAX_PAGES = 80",),
    "scripts/scrape_jobs.py": ("range(1, 35)", "range(1, 8)", "range(1, 60)", "range(1, 10)"),
    "scripts/repair_gyeonggi_support.py": ("range(1, 8)",),
}.items():
    text = (ROOT / rel).read_text(encoding="utf-8")
    hits = [x for x in bad if x in text]
    if hits:
        raise SystemExit(f"Low fixed pagination cap remains in {rel}: {hits}")

print(f"Pagination cap guard applied ({changed} source groups changed); 500 pages is emergency-only")
