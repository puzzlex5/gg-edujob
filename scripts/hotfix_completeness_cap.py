#!/usr/bin/env python3
"""Raise the legacy support-office page cap to an emergency-only safety ceiling.

The completeness crawler already marks a board incomplete when it reaches MAX_PAGES
without crossing the look-back boundary. The old value (80) was low enough to truncate
busy official boards. Keep a finite ceiling only to prevent infinite loops; reaching it
must remain an unhealthy/incomplete result.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts" / "complete_support_coverage.py"
s = p.read_text(encoding="utf-8")

old = "MAX_PAGES = 80"
new = "MAX_PAGES = 500  # emergency infinite-loop ceiling; reaching it is NOT complete"

if old in s:
    s = s.replace(old, new, 1)
elif "MAX_PAGES = 500" not in s:
    raise SystemExit("Unexpected completeness page-limit marker; refusing blind edit")

# Both Gyeonggi and Seoul completion predicates must still reject a ceiling hit.
needle = "pages < MAX_PAGES"
if s.count(needle) < 2:
    raise SystemExit("Completeness predicates no longer guard the safety ceiling")

p.write_text(s, encoding="utf-8")
print("Support-office pagination ceiling is emergency-only (500 pages); ceiling hits remain incomplete")
