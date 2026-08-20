#!/usr/bin/env python3
"""Harden the support-office completeness crawler before each hourly run.

This guard exists so a stale fixed-page implementation cannot silently truncate an office.
It is intentionally idempotent and fails closed if the expected crawler markers disappear.
"""
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "scripts" / "complete_support_coverage.py"
s = p.read_text(encoding="utf-8")

# Replace the legacy fixed ceiling with a high emergency ceiling. Normal termination must be
# driven by repeated/no-new rows or safely crossing the 90-day retention boundary.
s = s.replace("MAX_PAGES = 80", "MAX_PAGES = 500", 1)

# Any request failure after traversal starts means coverage is unproven; do not treat a partial
# crawl as complete merely because page 1 succeeded.
s = s.replace("access_error = page == 1", "access_error = True")

old_complete = "complete = (not access_error) and (crossed_old or (pages > 0 and not repeat and pages < MAX_PAGES))"
gg_complete = "complete = (not access_error) and (crossed_old or (pages > 0 and not repeat and pages < MAX_PAGES and (raw_rows > 0 or explicit_empty)))"
seoul_complete = "complete = (not access_error) and (crossed_old or (pages > 0 and not repeat and pages < MAX_PAGES and ((raw_rows > 0 and got_table) or explicit_empty)))"

count = s.count(old_complete)
if count == 2:
    s = s.replace(old_complete, gg_complete, 1)
    s = s.replace(old_complete, seoul_complete, 1)
elif count == 1:
    # One section may already have been hardened by a previous run. Harden the remaining one
    # according to whether its surrounding function tracks got_table.
    idx = s.index(old_complete)
    prefix = s[max(0, idx - 9000):idx]
    s = s.replace(old_complete, seoul_complete if "got_table" in prefix else gg_complete, 1)
elif count == 0:
    if gg_complete not in s or seoul_complete not in s:
        raise SystemExit("completeness termination marker changed; refusing an unsafe blind patch")
else:
    raise SystemExit(f"unexpected completeness marker count: {count}")

required = [
    "MAX_PAGES = 500",
    "access_error = True",
    "raw_rows > 0 or explicit_empty",
    "(raw_rows > 0 and got_table) or explicit_empty",
]
missing = [marker for marker in required if marker not in s]
if missing:
    raise SystemExit("support completeness hardening incomplete: " + ", ".join(missing))

p.write_text(s, encoding="utf-8")
print("Support-office completeness crawler hardened: dynamic traversal, fail-closed access errors, evidence-based completion")
