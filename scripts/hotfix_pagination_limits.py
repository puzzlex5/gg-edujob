#!/usr/bin/env python3
"""Remove unsafe fixed support-office pagination ceilings before collection.

The collectors still keep a high emergency ceiling to prevent an infinite crawl,
but normal termination is driven by no-new-row, repeated-page, or look-back
boundary detection in the collectors themselves.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMERGENCY_MAX = 500


def replace_required(path: Path, replacements):
    text = path.read_text(encoding="utf-8")
    original = text
    changed = []
    for old, new, label in replacements:
        if old in text:
            text = text.replace(old, new)
            changed.append(label)
    if text != original:
        path.write_text(text, encoding="utf-8")
    return text, changed


def main():
    complete = ROOT / "scripts" / "complete_support_coverage.py"
    text, changed = replace_required(
        complete,
        [
            ("MAX_PAGES = 80", f"MAX_PAGES = {EMERGENCY_MAX}", "complete-support 80→500"),
        ],
    )
    if f"MAX_PAGES = {EMERGENCY_MAX}" not in text:
        raise SystemExit("complete_support_coverage.py does not have the expected emergency page limit")
    if "for page in range(1, MAX_PAGES + 1):" not in text:
        raise SystemExit("complete support crawler no longer uses the guarded dynamic page loop")

    scraper = ROOT / "scripts" / "scrape_jobs.py"
    text, c2 = replace_required(
        scraper,
        [
            ("for page in range(1, 8):", f"for page in range(1, {EMERGENCY_MAX + 1}):", "Gyeonggi primary 7→500"),
            ("for page in range(1, 10):", f"for page in range(1, {EMERGENCY_MAX + 1}):", "Seoul primary 9→500"),
            ("for page in range(1, 61):", f"for page in range(1, {EMERGENCY_MAX + 1}):", "primary 60→500"),
        ],
    )
    changed += c2
    # Require the support-office functions to carry the high emergency ceiling.
    for fn, next_fn in (("def scrape_mircms_board", "def scrape_gyeonggi_office"), ("def scrape_seoul_office", "def dedupe_merge")):
        if fn not in text or next_fn not in text:
            raise SystemExit(f"collector function marker missing: {fn}")
        block = text[text.index(fn):text.index(next_fn)]
        if f"range(1, {EMERGENCY_MAX + 1})" not in block:
            raise SystemExit(f"unsafe fixed page ceiling remains in {fn}")

    repair = ROOT / "scripts" / "repair_gyeonggi_support.py"
    text, c3 = replace_required(
        repair,
        [
            ("for page in range(1, 8):", f"for page in range(1, {EMERGENCY_MAX + 1}):", "Gyeonggi repair 7→500"),
            ("for page in range(1, 61):", f"for page in range(1, {EMERGENCY_MAX + 1}):", "Gyeonggi repair 60→500"),
        ],
    )
    changed += c3
    if f"range(1, {EMERGENCY_MAX + 1})" not in text:
        raise SystemExit("repair_gyeonggi_support.py still lacks the high emergency ceiling")

    print("Pagination guard ready; " + (", ".join(changed) if changed else "already patched"))


if __name__ == "__main__":
    main()
