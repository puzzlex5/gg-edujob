#!/usr/bin/env python3
"""Idempotently enforce the 90-day Gyeonggi central retention guard.

This hardener deliberately accepts equivalent pagination hardening installed by earlier
steps in the same workflow. It only mutates legacy collector shapes; already-hardened
collector variants are validated rather than rewritten.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/scrape_jobs.py"
s = p.read_text(encoding="utf-8")

start = s.find("def scrape_gyeonggi_central():")
end = s.find("# -------------------- 경기 교육지원청", start)
if start < 0 or end < 0:
    raise SystemExit("Cannot locate Gyeonggi central collector section")
block = s[start:end]

# Central crawl must include recently closed postings; retention is enforced by date below.
block = block.replace('"srchEcptDl":"Y"', '"srchEcptDl":""')

old_init = (
    "    jobs, seen_ids = [], set()\n"
    "    for code, (cat_name, ui_type) in GYEONGGI_CATEGORIES.items():\n"
)
new_init = old_init + "        consecutive_old_pages = 0\n"
if "consecutive_old_pages = 0" not in block:
    if old_init not in block:
        raise SystemExit("Cannot locate Gyeonggi central category initialization")
    block = block.replace(old_init, new_init, 1)

if "page_dates = []" not in block:
    old_added = "            added = 0\n            for li in rows:\n"
    new_added = "            added = 0\n            page_dates = []\n            for li in rows:\n"
    if old_added not in block:
        raise SystemExit("Cannot locate Gyeonggi central page initialization")
    block = block.replace(old_added, new_added, 1)

if "if registered: page_dates.append(registered)" not in block:
    old_registered = (
        '                registered = next((date_norm(x) for x in top if "등록일" in x), "")\n'
        '                title_el = li.select_one(".cont_tit")\n'
    )
    new_registered = (
        '                registered = next((date_norm(x) for x in top if "등록일" in x), "")\n'
        '                if registered: page_dates.append(registered)\n'
        '                title_el = li.select_one(".cont_tit")\n'
    )
    if old_registered not in block:
        raise SystemExit("Cannot locate Gyeonggi central registration date parsing")
    block = block.replace(old_registered, new_registered, 1)

if "if registered and not recent_enough(registered, 90):" not in block:
    old_append = (
        "                region = find_region(body_text, GYEONGGI_REGIONS)\n"
        "                jobs.append({\n"
    )
    new_append = (
        "                region = find_region(body_text, GYEONGGI_REGIONS)\n"
        "                if registered and not recent_enough(registered, 90):\n"
        "                    continue\n"
        "                jobs.append({\n"
    )
    if old_append not in block:
        raise SystemExit("Cannot locate Gyeonggi central append point")
    block = block.replace(old_append, new_append, 1)

# Earlier pagination hardeners may replace `added == 0` with stronger repeated-page
# evidence based on the complete page ID sequence. Both are compatible with the
# retention boundary below; do not require the weaker legacy marker when the stronger
# page-signature guard is present.
legacy_stop_present = "if added == 0" in block
signature_stop_present = all(marker in block for marker in (
    "if not page_ids: break",
    "page_signature = tuple(page_ids)",
    "if page_signature == previous_page_ids: break",
    "previous_page_ids = page_signature",
))
retention_stop_present = all(marker in block for marker in (
    "fully_dated = len(page_dates) == added",
    "consecutive_old_pages >= 2",
))
if not ((legacy_stop_present or signature_stop_present) and retention_stop_present):
    old_tail = "            if added == 0 or len(rows) < 45: break\n            time.sleep(.05)\n"
    new_tail = (
        "            if added == 0: break\n"
        "            fully_dated = len(page_dates) == added\n"
        "            if fully_dated and page_dates and all(not recent_enough(d, 90) for d in page_dates):\n"
        "                consecutive_old_pages += 1\n"
        "            else:\n"
        "                consecutive_old_pages = 0\n"
        "            if consecutive_old_pages >= 2: break\n"
        "            if len(rows) < 45: break\n"
        "            time.sleep(.05)\n"
    )
    if old_tail not in block:
        raise SystemExit("Cannot locate equivalent Gyeonggi central stop semantics")
    block = block.replace(old_tail, new_tail, 1)

required = (
    '"srchEcptDl":""',
    "consecutive_old_pages = 0",
    "page_dates = []",
    "if registered: page_dates.append(registered)",
    "if registered and not recent_enough(registered, 90):",
    "fully_dated = len(page_dates) == added",
    "consecutive_old_pages >= 2",
)
for marker in required:
    if marker not in block:
        raise SystemExit(f"Gyeonggi central 90-day hardening missing: {marker}")
if not ("if added == 0" in block or all(marker in block for marker in (
    "if not page_ids: break",
    "page_signature = tuple(page_ids)",
    "if page_signature == previous_page_ids: break",
    "previous_page_ids = page_signature",
))):
    raise SystemExit("Gyeonggi central pagination stop evidence is missing")

s = s[:start] + block + s[end:]
p.write_text(s, encoding="utf-8")

# Publication verification must stay retention-aware so a corrected 90-day population is not
# mistaken for a catastrophic drop from an older over-collected baseline.
vp = ROOT / "scripts/verify_jobs.py"
vs = vp.read_text(encoding="utf-8")
verifier_markers = (
    "def authoritative_central_count(province):",
    "verified_retention_cleanup = (",
    "abs(after - authoritative) <= tolerance",
    "before > authoritative * 1.5",
)
if not all(marker in vs for marker in verifier_markers):
    raise SystemExit("Retention-aware central-drop verifier is missing")

print("Gyeonggi central retention hardening verified (idempotent across equivalent pagination variants)")
