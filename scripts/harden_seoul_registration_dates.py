#!/usr/bin/env python3
"""Prevent Seoul support-office row fallbacks from inventing registration dates.

Some Seoul JOL11 tables omit a true 등록일/작성일 column while still showing application or
work-period dates. The legacy fallback selected the last date visible in the row, so a future
work/end date could be stored as registered/applyStart and only removed later by the sanitizer.
This hardener keeps the fallback conservative: use a row-derived registration date only when
there is exactly one distinct non-future date. Ambiguous rows remain undated instead of guessing.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TODAY_EXPR = 'NOW.strftime("%Y/%m/%d")'


def patch_seoul_segment(path: Path, start_marker: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit(f"Cannot locate Seoul parser marker in {path.name}: {start_marker}")
    head, tail = text[:start], text[start:]

    old_compact = 'ds=all_dates(clean(tr.get_text(" ",strip=True))); registered=ds[-1] if ds else ""'
    new_compact = (
        'ds=all_dates(clean(tr.get_text(" ",strip=True))); '
        f'today_s={TODAY_EXPR}; '
        'plausible=list(dict.fromkeys(d for d in ds if d and d <= today_s)); '
        'registered=plausible[0] if len(plausible)==1 else ""'
    )
    tail = tail.replace(old_compact, new_compact)

    old_multiline = '''ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                    registered = ds[-1] if ds else ""'''
    new_multiline = '''ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                    today_s = NOW.strftime("%Y/%m/%d")\n                    plausible = list(dict.fromkeys(d for d in ds if d and d <= today_s))\n                    registered = plausible[0] if len(plausible) == 1 else ""'''
    tail = tail.replace(old_multiline, new_multiline)

    old_multiline_deep = '''ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                                registered = ds[-1] if ds else ""'''
    new_multiline_deep = '''ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                                today_s = NOW.strftime("%Y/%m/%d")\n                                plausible = list(dict.fromkeys(d for d in ds if d and d <= today_s))\n                                registered = plausible[0] if len(plausible) == 1 else ""'''
    tail = tail.replace(old_multiline_deep, new_multiline_deep)

    segment = tail
    if 'registered=ds[-1] if ds else ""' in segment or 'registered = ds[-1] if ds else ""' in segment:
        raise SystemExit(f"Unsafe Seoul registration-date fallback remains in {path.name}")
    if "plausible" not in segment:
        raise SystemExit(f"Seoul registration-date hardening marker missing in {path.name}")

    path.write_text(head + tail, encoding="utf-8")


patch_seoul_segment(ROOT / "scripts/scrape_jobs.py", "def scrape_seoul_office")
patch_seoul_segment(ROOT / "scripts/complete_support_coverage.py", "def seoul_board")
print("Seoul registration-date fallbacks hardened")
