#!/usr/bin/env python3
"""Normalize fast crawl pagination and population semantics before every fast crawl.

The fast path must traverse the same current/recent population that the independent 90-day
reconciliation audits. In particular, Gyeonggi central must not enable the active-only
(마감제외) filter, because doing so systematically drops recently closed postings and forces the
reconciliation safety net to restore thousands of otherwise valid rows after every fresh crawl.

Support-office pagination is evidence-driven rather than page-count-driven. A board may stop only
when it produces no new semantic rows (including a repeated page) or when two consecutive fully
dated pages are entirely older than the 90-day retention boundary. Undated or mixed-date pages do
not prove the boundary and therefore continue traversal. Fixed numeric page ceilings are removed
from both Gyeonggi and Seoul support-office collectors.
"""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/scrape_jobs.py"
s = p.read_text(encoding="utf-8")

# Keep the legacy constant only as a migration marker for older checked-in collectors. It must not
# be used by support-office crawl loops after this hardener runs.
if "FAST_MAX_PAGES = 60" not in s:
    marker = 'BOARD_EXCLUDE = re.compile(r"구직|인력풀|합격|인사정보|인사발령|공지사항|자료실")\n'
    if marker not in s:
        raise SystemExit("Cannot locate collector constants marker")
    s = s.replace(marker, marker + "FAST_MAX_PAGES = 60  # legacy migration marker; support loops are evidence-driven\n", 1)

# Gyeonggi central: collect the full recent 90-day population, not only currently open postings.
# This intentionally matches verify_gyeonggi_central_90d.py and source reconciliation semantics.
active_filter = '"srchEcptDl":"Y"'
if active_filter in s:
    s = s.replace(active_filter, '"srchEcptDl":""', 1)
if '"srchEcptDl":""' not in s:
    raise SystemExit("Cannot disable Gyeonggi central active-only filter")

# Normalize every historical support-office fixed loop to the current legacy form first.
for old in (
    "for page in range(1, 8):",
    "for page in range(1, 10):",
    "for page in range(1, 61):",
):
    s = s.replace(old, "for page in range(1, FAST_MAX_PAGES + 1):")

# A page with no matching recruitment title is not proof that later rows are absent.
s = s.replace("        if page_recent == 0 and page >= 2: break\n", "")
s = s.replace("        if page_recent==0 and page>=2: break\n", "")

# ---------- Gyeonggi support boards ----------
old_init = "    raw_rows = 0; explicit_empty = False; pages_scanned = 0\n"
new_init = "    raw_rows = 0; explicit_empty = False; pages_scanned = 0; consecutive_old_pages = 0\n"
if new_init not in s:
    if old_init not in s:
        raise SystemExit("Cannot locate Gyeonggi fast-board initialization")
    s = s.replace(old_init, new_init, 1)

old_loop = "    for page in range(1, FAST_MAX_PAGES + 1):\n        u = with_query(board, currPage=page)\n"
new_loop = "    page = 1\n    while True:\n        u = with_query(board, currPage=page)\n"
if new_loop not in s:
    if old_loop not in s:
        raise SystemExit("Cannot locate Gyeonggi support pagination loop")
    s = s.replace(old_loop, new_loop, 1)

old_page = "        page_candidates = 0; page_recent = 0\n"
new_page = "        page_candidates = 0; page_recent = 0; page_dates = []\n"
if new_page not in s:
    if old_page not in s:
        raise SystemExit("Cannot locate Gyeonggi fast-board page counters")
    s = s.replace(old_page, new_page, 1)

old_registered = '                if not registered:\n                    ds = all_dates(row_text); registered = ds[-1] if ds else ""\n                if registered and not recent_enough(registered, 90): continue\n'
new_registered = '                if not registered:\n                    ds = all_dates(row_text); registered = ds[-1] if ds else ""\n                if registered: page_dates.append(registered)\n                if registered and not recent_enough(registered, 90): continue\n'
if new_registered not in s:
    if old_registered not in s:
        raise SystemExit("Cannot locate Gyeonggi registration-date filter")
    s = s.replace(old_registered, new_registered, 1)

old_stop = "        if page_candidates == 0: break\n        if page_dates and all(not recent_enough(d, 90) for d in page_dates):\n            consecutive_old_pages += 1\n        else:\n            consecutive_old_pages = 0\n        if consecutive_old_pages >= 2: break\n        time.sleep(.04)\n"
new_stop = "        if page_candidates == 0: break\n        if page_dates and all(not recent_enough(d, 90) for d in page_dates):\n            consecutive_old_pages += 1\n        else:\n            consecutive_old_pages = 0\n        if consecutive_old_pages >= 2: break\n        page += 1\n        time.sleep(.04)\n"
if new_stop not in s:
    # Older collector may not yet contain lookback stopping.
    legacy_stop = "        if page_candidates == 0: break\n        time.sleep(.04)\n"
    if legacy_stop not in s:
        raise SystemExit("Cannot locate Gyeonggi fast-board stop block")
    s = s.replace(legacy_stop, new_stop, 1)

# Fixed-cap metadata/classification is obsolete once traversal is evidence-driven.
s = s.replace(
    'return out, {"rawRows":raw_rows,"explicitEmpty":explicit_empty,"pagesScanned":pages_scanned,"capHit":pages_scanned >= FAST_MAX_PAGES}',
    'return out, {"rawRows":raw_rows,"explicitEmpty":explicit_empty,"pagesScanned":pages_scanned}',
    1,
)
s = s.replace('    cap_hit = any(x.get("capHit") for x in meta)\n', '', 1)
s = s.replace(
    '    elif cap_hit:\n        state, ok, msg = "warning", False, "빠른 수집 비상 페이지 상한 도달 · 깊은 감사 필요"\n',
    '',
    1,
)

# ---------- Seoul support boards ----------
old_seoul_init = "    out, seen = [], set(); raw_rows=0; explicit_empty=False; got_table=False\n"
new_seoul_init = "    out, seen = [], set(); raw_rows=0; explicit_empty=False; got_table=False; consecutive_old_pages=0\n"
if new_seoul_init not in s:
    if old_seoul_init not in s:
        raise SystemExit("Cannot locate Seoul support initialization")
    s = s.replace(old_seoul_init, new_seoul_init, 1)

old_seoul_loop = "    for page in range(1, FAST_MAX_PAGES + 1):\n        r = get(board, params={\"pageIndex\":page})\n"
new_seoul_loop = "    page = 1\n    while True:\n        r = get(board, params={\"pageIndex\":page})\n"
if new_seoul_loop not in s:
    if old_seoul_loop not in s:
        raise SystemExit("Cannot locate Seoul support pagination loop")
    s = s.replace(old_seoul_loop, new_seoul_loop, 1)

old_seoul_page = "        page_raw=0; page_recent=0\n"
new_seoul_page = "        page_raw=0; page_recent=0; page_dates=[]\n"
if new_seoul_page not in s:
    if old_seoul_page not in s:
        raise SystemExit("Cannot locate Seoul support page counters")
    s = s.replace(old_seoul_page, new_seoul_page, 1)

old_seoul_registered = '                if registered and not recent_enough(registered,90): continue\n                page_recent+=1\n'
new_seoul_registered = '                if registered: page_dates.append(registered)\n                if registered and not recent_enough(registered,90): continue\n                page_recent+=1\n'
if new_seoul_registered not in s:
    if old_seoul_registered not in s:
        raise SystemExit("Cannot locate Seoul registration-date filter")
    s = s.replace(old_seoul_registered, new_seoul_registered, 1)

old_seoul_stop = "        if page_raw==0: break\n        time.sleep(.04)\n"
new_seoul_stop = "        if page_raw==0: break\n        if page_dates and all(not recent_enough(d, 90) for d in page_dates):\n            consecutive_old_pages += 1\n        else:\n            consecutive_old_pages = 0\n        if consecutive_old_pages >= 2: break\n        page += 1\n        time.sleep(.04)\n"
if new_seoul_stop not in s:
    if old_seoul_stop not in s:
        raise SystemExit("Cannot locate Seoul support stop block")
    s = s.replace(old_seoul_stop, new_seoul_stop, 1)

# No support-office collector may retain a fixed numeric page loop after this phase.
support_block = s[s.find("def scrape_mircms_board"):s.find("def dedupe_merge")]
for forbidden in (
    "range(1, 8)", "range(1, 10)", "range(1, 61)",
    "range(1, FAST_MAX_PAGES + 1)",
):
    if forbidden in support_block:
        raise SystemExit(f"Fixed support-office page ceiling remains: {forbidden}")

required = (
    '"srchEcptDl":""',
    "consecutive_old_pages >= 2",
    "page += 1",
)
for marker in required:
    if marker not in s:
        raise SystemExit(f"Fast completeness hardening missing: {marker}")

p.write_text(s, encoding="utf-8")

# Central portal pagination must also be hardened for ordinary fast crawls.
runpy.run_path(str(ROOT / "scripts/harden_central_pagination.py"), run_name="__main__")

# Stable posting IDs and conservative deduplication are mandatory pre-crawl hardening.
runpy.run_path(str(ROOT / "scripts/harden_identity_dedupe.py"), run_name="__main__")

# A fresh 38-source stable-ID reconciliation is authoritative for publication.
runpy.run_path(str(ROOT / "scripts/harden_status_semantics.py"), run_name="__main__")

print("Fast population, support pagination, central pagination, identity, and status semantics hardened")
