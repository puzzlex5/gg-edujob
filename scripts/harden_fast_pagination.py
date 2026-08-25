#!/usr/bin/env python3
"""Normalize fast and central pagination before every fast crawl.

Support-office collectors historically used fixed 7/9-page loops and the two central portals
used fixed 34/59-page loops. This hardener is deliberately idempotent: it normalizes the support
office collectors to FAST_MAX_PAGES and then invokes the central hardener so ordinary fast runs
cannot silently retain the legacy central caps. Natural empty/repeated/final-page evidence remains
the normal stop condition; the numeric maxima are emergency ceilings only.

The fast path must not duplicate the full deep audit. Once a support-office board has produced
two consecutive pages whose dated rows are all older than the 90-day publication population,
the fast crawl may stop. This is deliberately conservative: undated or mixed-date pages never
trigger the boundary stop, and the independent reconciliation/deep-audit paths remain responsible
for proving full 90-day completeness.
"""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/scrape_jobs.py"
s = p.read_text(encoding="utf-8")

if "FAST_MAX_PAGES = 60" not in s:
    marker = 'BOARD_EXCLUDE = re.compile(r"구직|인력풀|합격|인사정보|인사발령|공지사항|자료실")\n'
    if marker not in s:
        raise SystemExit("Cannot locate collector constants marker")
    s = s.replace(marker, marker + "FAST_MAX_PAGES = 60  # emergency ceiling; normal stop is empty/repeated page\n", 1)

# Normalize every known historical support-office loop form. hotfix_support_office.py can run
# before this script and convert 7/9 to a literal 60, so literal 60 is not an error.
for old in (
    "for page in range(1, 8):",
    "for page in range(1, 10):",
    "for page in range(1, 61):",
):
    s = s.replace(old, "for page in range(1, FAST_MAX_PAGES + 1):")

# A page with no matching recruitment title is not proof that later rows are absent.
# Empty/repeated pages and a proven chronological lookback boundary are legitimate fast stops.
s = s.replace("        if page_recent == 0 and page >= 2: break\n", "")
s = s.replace("        if page_recent==0 and page>=2: break\n", "")

# Gyeonggi fast boards: stop after two consecutive fully-dated pages are all older than 90 days.
# Do not stop on undated/mixed pages; that keeps the rule fail-safe for irregular boards.
old_init = "    raw_rows = 0; explicit_empty = False; pages_scanned = 0\n"
new_init = "    raw_rows = 0; explicit_empty = False; pages_scanned = 0; consecutive_old_pages = 0\n"
if new_init not in s:
    if old_init not in s:
        raise SystemExit("Cannot locate Gyeonggi fast-board initialization")
    s = s.replace(old_init, new_init, 1)

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

old_stop = "        if page_candidates == 0: break\n        time.sleep(.04)\n"
new_stop = "        if page_candidates == 0: break\n        if page_dates and all(not recent_enough(d, 90) for d in page_dates):\n            consecutive_old_pages += 1\n        else:\n            consecutive_old_pages = 0\n        if consecutive_old_pages >= 2: break\n        time.sleep(.04)\n"
if new_stop not in s:
    if old_stop not in s:
        raise SystemExit("Cannot locate Gyeonggi fast-board stop block")
    s = s.replace(old_stop, new_stop, 1)

# Gyeonggi metadata: record emergency-cap contact explicitly.
old_return = 'return out, {"rawRows":raw_rows,"explicitEmpty":explicit_empty,"pagesScanned":pages_scanned}'
new_return = 'return out, {"rawRows":raw_rows,"explicitEmpty":explicit_empty,"pagesScanned":pages_scanned,"capHit":pages_scanned >= FAST_MAX_PAGES}'
if old_return in s:
    s = s.replace(old_return, new_return, 1)
elif new_return not in s:
    raise SystemExit("Cannot locate Gyeonggi fast-board metadata return")

old_meta = '    explicit_empty = any(x["explicitEmpty"] for x in meta)\n'
new_meta = '    explicit_empty = any(x["explicitEmpty"] for x in meta)\n    cap_hit = any(x.get("capHit") for x in meta)\n'
if new_meta not in s:
    if old_meta not in s:
        raise SystemExit("Cannot locate Gyeonggi support-office metadata aggregation")
    s = s.replace(old_meta, new_meta, 1)

old_state = '    if not boards:\n        state, ok, msg = "error", False, "실제 채용 게시판을 찾지 못함"\n    elif raw_total > 0:\n'
new_state = '    if not boards:\n        state, ok, msg = "error", False, "실제 채용 게시판을 찾지 못함"\n    elif cap_hit:\n        state, ok, msg = "warning", False, "빠른 수집 비상 페이지 상한 도달 · 깊은 감사 필요"\n    elif raw_total > 0:\n'
if new_state not in s:
    if old_state not in s:
        raise SystemExit("Cannot locate Gyeonggi support-office health classification")
    s = s.replace(old_state, new_state, 1)

required = (
    "FAST_MAX_PAGES = 60",
    "range(1, FAST_MAX_PAGES + 1)",
    '"capHit":pages_scanned >= FAST_MAX_PAGES',
    "consecutive_old_pages >= 2",
)
for marker in required:
    if marker not in s:
        raise SystemExit(f"Fast pagination hardening missing: {marker}")

# The old fixed support-office loops must not survive this phase.
for forbidden in ("range(1, 8)", "range(1, 10)", "range(1, 61)"):
    block = s[s.find("def scrape_mircms_board"):s.find("def dedupe_merge")]
    if forbidden in block:
        raise SystemExit(f"Fixed support-office page ceiling remains: {forbidden}")

p.write_text(s, encoding="utf-8")

# Central portal pagination must be hardened for ordinary fast crawls too, not only during the
# separate source-ID reconciliation workflow. Otherwise the checked-in 34/59-page legacy loops
# can silently truncate fast data between deep audits.
runpy.run_path(str(ROOT / "scripts/harden_central_pagination.py"), run_name="__main__")

# Stable posting IDs and conservative deduplication are part of the same mandatory pre-crawl
# hardening phase. Generic titles from different schools must not merge.
runpy.run_path(str(ROOT / "scripts/harden_identity_dedupe.py"), run_name="__main__")

# A fresh 38-source stable-ID reconciliation is authoritative for publication. It must not be
# masked by an older support-count proof, and post-success git synchronization races must not
# overwrite a verified complete run as a collector failure.
runpy.run_path(str(ROOT / "scripts/harden_status_semantics.py"), run_name="__main__")

print("Fast support-office, central pagination, identity, and status semantics hardened")
