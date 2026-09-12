#!/usr/bin/env python3
"""Fail-closed and idempotent Seoul registration-date hardening."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TODAY_EXPR = 'NOW.strftime("%Y/%m/%d")'


def patch_target(path, marker):
    text = path.read_text(encoding="utf-8")
    start = text.find(marker)
    if start < 0:
        if path.name == "complete_support_coverage.py":
            print("Seoul coverage parser already refactored; no-op")
            return
        raise SystemExit("Cannot locate required Seoul production parser")
    end = text.find("\ndef ", start + len(marker))
    if end < 0:
        end = len(text)
    head, body, tail = text[:start], text[start:end], text[end:]

    body = body.replace(
        'ds=all_dates(clean(tr.get_text(" ",strip=True))); registered=ds[-1] if ds else ""',
        'ds=all_dates(clean(tr.get_text(" ",strip=True))); today_s=NOW.strftime("%Y/%m/%d"); plausible=list(dict.fromkeys(d for d in ds if d and d <= today_s)); registered=plausible[0] if len(plausible)==1 else ""',
    )
    body = body.replace(
        'ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                    registered = ds[-1] if ds else ""',
        'ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                    today_s = NOW.strftime("%Y/%m/%d")\n                    plausible = list(dict.fromkeys(d for d in ds if d and d <= today_s))\n                    registered = plausible[0] if len(plausible) == 1 else ""',
    )
    body = body.replace(
        'ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                                registered = ds[-1] if ds else ""',
        'ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                                today_s = NOW.strftime("%Y/%m/%d")\n                                plausible = list(dict.fromkeys(d for d in ds if d and d <= today_s))\n                                registered = plausible[0] if len(plausible) == 1 else ""',
    )

    if re.search(r"registered\s*=\s*ds\s*\[\s*-1\s*\]", body):
        raise SystemExit("Unsafe Seoul registration-date fallback remains")
    compact = re.sub(r"\s+", "", body)
    explicit = "registered=date_norm(first_of(vals,[\"등록일\",\"작성일\"]))" in compact
    explicit = explicit or ("registered=date_norm(first_of(vals," in compact and "등록일" in body and "작성일" in body)
    if "plausible" not in body and not explicit:
        raise SystemExit("Seoul registration-date hardening invariant missing")
    new_text = head + body + tail
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
        print("Seoul registration-date hardening applied", path.name)
    else:
        print("Seoul registration-date hardening already applied", path.name)

patch_target(ROOT / "scripts/scrape_jobs.py", "def scrape_seoul_office")
patch_target(ROOT / "scripts/complete_support_coverage.py", "def seoul_board")
