#!/usr/bin/env python3
"""Keep Seoul support-office registration-date parsing fail-closed and idempotent.

Older Seoul JOL11 row parsers guessed the registration date from the last date visible
in a row. That could turn an application/work-period date into ``registered``. This
hardener still upgrades the legacy fallback when it sees it, but it no longer requires
one specific implementation string after newer parsers have already moved to an
explicit 등록일/작성일 label. The invariant is semantic:

* no ``registered = ds[-1] ...`` fallback may remain in the target function; and
* the function must either use the conservative single-plausible-date fallback or an
  explicit labelled registration-date parser.

That makes parser improvements compatible with the hardening step while preserving the
fail-closed guard against reintroducing an unsafe last-date fallback.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TODAY_EXPR = 'NOW.strftime("%Y/%m/%d")'


def function_segment(text: str, start_marker: str) -> tuple[str, str, str]:
    """Return text before, the target top-level function, and text after it."""
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit(f"Cannot locate Seoul parser marker: {start_marker}")
    next_def = text.find("\ndef ", start + len(start_marker))
    if next_def < 0:
        next_def = len(text)
    return text[:start], text[start:next_def], text[next_def:]


def has_explicit_registration_parser(segment: str) -> bool:
    """Accept labelled 등록일/작성일 parsing without binding to formatting details."""
    compact = re.sub(r"\s+", "", segment)
    return (
        "registered=date_norm(first_of(vals,[\"등록일\",\"작성일\"]))" in compact
        or "registered=date_norm(first_of(vals,['등록일','작성일']))" in compact
        or (
            "registered=date_norm(first_of(vals," in compact
            and "등록일" in segment
            and "작성일" in segment
        )
    )


def patch_seoul_segment(path: Path, start_marker: str) -> None:
    text = path.read_text(encoding="utf-8")
    head, segment, tail = function_segment(text, start_marker)

    old_compact = 'ds=all_dates(clean(tr.get_text(" ",strip=True))); registered=ds[-1] if ds else ""'
    new_compact = (
        'ds=all_dates(clean(tr.get_text(" ",strip=True))); '
        f'today_s={TODAY_EXPR}; '
        'plausible=list(dict.fromkeys(d for d in ds if d and d <= today_s)); '
        'registered=plausible[0] if len(plausible)==1 else ""'
    )
    segment = segment.replace(old_compact, new_compact)

    old_multiline = '''ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                    registered = ds[-1] if ds else ""'''
    new_multiline = '''ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                    today_s = NOW.strftime("%Y/%m/%d")\n                    plausible = list(dict.fromkeys(d for d in ds if d and d <= today_s))\n                    registered = plausible[0] if len(plausible) == 1 else ""'''
    segment = segment.replace(old_multiline, new_multiline)

    old_multiline_deep = '''ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                                registered = ds[-1] if ds else ""'''
    new_multiline_deep = '''ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                                today_s = NOW.strftime("%Y/%m/%d")\n                                plausible = list(dict.fromkeys(d for d in ds if d and d <= today_s))\n                                registered = plausible[0] if len(plausible) == 1 else ""'''
    segment = segment.replace(old_multiline_deep, new_multiline_deep)

    # Fail closed on any remaining last-date assignment, including variants that the
    # legacy text replacements above did not recognize.
    if re.search(r"registered\s*=\s*ds\s*\[\s*-1\s*\]", segment):
        raise SystemExit(f"Unsafe Seoul registration-date fallback remains in {path.name}")

    conservative_fallback = "plausible" in segment
    explicit_label_parser = has_explicit_registration_parser(segment)
    if not (conservative_fallback or explicit_label_parser):
        raise SystemExit(f"Seoul registration-date hardening invariant missing in {path.name}")

    path.write_text(head + segment + tail, encoding="utf-8")


patch_seoul_segment(ROOT / "scripts/scrape_jobs.py", "def scrape_seoul_office")
patch_seoul_segment(ROOT / "scripts/complete_support_coverage.py", "def seoul_board")
print("Seoul registration-date fallbacks hardened")
