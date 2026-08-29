#!/usr/bin/env python3
"""Idempotently align the fast crawl with the complete recent recruitment population.

Support-office traversal is evidence-driven: stop only on no new semantic rows/repeated pages,
or after two consecutive *fully dated* pages are entirely older than the 90-day retention
boundary. Numeric support-page ceilings must never control traversal.
"""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/scrape_jobs.py"
s = p.read_text(encoding="utf-8")


def replace_once(text, old, new, label):
    if new in text:
        return text
    if old not in text:
        raise SystemExit(f"Cannot locate {label}")
    return text.replace(old, new, 1)


def transform_between(text, start_marker, end_marker, fn):
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit(f"Cannot locate section {start_marker}")
    return text[:start] + fn(text[start:end]) + text[end:]


# Retain this literal only as a migration/assertion marker for older checked-in collectors.
# Evidence-driven support loops below must not reference it.
if "FAST_MAX_PAGES = 60" not in s:
    marker = 'BOARD_EXCLUDE = re.compile(r"구직|인력풀|합격|인사정보|인사발령|공지사항|자료실")\n'
    if marker not in s:
        raise SystemExit("Cannot locate collector constants marker")
    s = s.replace(marker, marker + "FAST_MAX_PAGES = 60  # legacy migration marker only; support loops are evidence-driven\n", 1)

# Gyeonggi central must include recently closed postings just like the authoritative 90-day audit.
s = s.replace('"srchEcptDl":"Y"', '"srchEcptDl":""')
if '"srchEcptDl":""' not in s:
    raise SystemExit("Cannot disable Gyeonggi central active-only filter")


def harden_gyeonggi(block):
    block = block.replace("    raw_rows = 0; explicit_empty = False; pages_scanned = 0\n",
                          "    raw_rows = 0; explicit_empty = False; pages_scanned = 0; consecutive_old_pages = 0\n", 1)
    block = block.replace("    for page in range(1, FAST_MAX_PAGES + 1):\n        u = with_query(board, currPage=page)\n",
                          "    page = 1\n    while True:\n        u = with_query(board, currPage=page)\n", 1)
    block = block.replace("        page_candidates = 0; page_recent = 0\n",
                          "        page_candidates = 0; page_recent = 0; page_dates = []\n", 1)
    old_reg = '                if not registered:\n                    ds = all_dates(row_text); registered = ds[-1] if ds else ""\n                if registered and not recent_enough(registered, 90): continue\n'
    new_reg = '                if not registered:\n                    ds = all_dates(row_text); registered = ds[-1] if ds else ""\n                if registered: page_dates.append(registered)\n                if registered and not recent_enough(registered, 90): continue\n'
    if "if registered: page_dates.append(registered)" not in block:
        if old_reg not in block:
            raise SystemExit("Cannot locate Gyeonggi registration-date filter")
        block = block.replace(old_reg, new_reg, 1)

    # Older revisions already tracked old pages but still used the numeric for-loop. Normalize the
    # stop tail independently so repeated hardening is safe.
    old_tail = "        if page_candidates == 0: break\n        if page_dates and all(not recent_enough(d, 90) for d in page_dates):\n            consecutive_old_pages += 1\n        else:\n            consecutive_old_pages = 0\n        if consecutive_old_pages >= 2: break\n        time.sleep(.04)\n"
    new_tail = "        if page_candidates == 0: break\n        fully_dated = len(page_dates) == page_candidates\n        if fully_dated and page_dates and all(not recent_enough(d, 90) for d in page_dates):\n            consecutive_old_pages += 1\n        else:\n            consecutive_old_pages = 0\n        if consecutive_old_pages >= 2: break\n        page += 1\n        time.sleep(.04)\n"
    legacy_tail = "        if page_candidates == 0: break\n        time.sleep(.04)\n"
    if new_tail not in block:
        if old_tail in block:
            block = block.replace(old_tail, new_tail, 1)
        elif legacy_tail in block:
            block = block.replace(legacy_tail, new_tail, 1)
        else:
            raise SystemExit("Cannot locate Gyeonggi fast-board stop block")

    block = block.replace(
        'return out, {"rawRows":raw_rows,"explicitEmpty":explicit_empty,"pagesScanned":pages_scanned,"capHit":pages_scanned >= FAST_MAX_PAGES}',
        'return out, {"rawRows":raw_rows,"explicitEmpty":explicit_empty,"pagesScanned":pages_scanned}', 1)
    return block


s = transform_between(s, "def scrape_mircms_board", "def scrape_gyeonggi_office", harden_gyeonggi)
s = s.replace('    cap_hit = any(x.get("capHit") for x in meta)\n', '', 1)
s = s.replace('    elif cap_hit:\n        state, ok, msg = "warning", False, "빠른 수집 비상 페이지 상한 도달 · 깊은 감사 필요"\n', '', 1)


def harden_seoul(block):
    block = block.replace(
        "    out, seen = [], set(); raw_rows=0; explicit_empty=False; got_table=False\n",
        "    out, seen = [], set(); raw_rows=0; explicit_empty=False; got_table=False; consecutive_old_pages=0\n", 1)
    block = block.replace(
        "    for page in range(1, FAST_MAX_PAGES + 1):\n        r = get(board, params={\"pageIndex\":page})\n",
        "    page = 1\n    while True:\n        r = get(board, params={\"pageIndex\":page})\n", 1)
    block = block.replace("        page_raw=0; page_recent=0\n", "        page_raw=0; page_recent=0; page_dates=[]\n", 1)
    old_reg = '                if registered and not recent_enough(registered,90): continue\n                page_recent+=1\n'
    new_reg = '                if registered: page_dates.append(registered)\n                if registered and not recent_enough(registered,90): continue\n                page_recent+=1\n'
    if "if registered: page_dates.append(registered)" not in block:
        if old_reg not in block:
            raise SystemExit("Cannot locate Seoul registration-date filter")
        block = block.replace(old_reg, new_reg, 1)

    old_tail = "        if page_raw==0: break\n        time.sleep(.04)\n"
    old_hardened_tail = "        if page_raw==0: break\n        if page_dates and all(not recent_enough(d, 90) for d in page_dates):\n            consecutive_old_pages += 1\n        else:\n            consecutive_old_pages = 0\n        if consecutive_old_pages >= 2: break\n        page += 1\n        time.sleep(.04)\n"
    new_tail = "        if page_raw==0: break\n        fully_dated = len(page_dates) == page_raw\n        if fully_dated and page_dates and all(not recent_enough(d, 90) for d in page_dates):\n            consecutive_old_pages += 1\n        else:\n            consecutive_old_pages = 0\n        if consecutive_old_pages >= 2: break\n        page += 1\n        time.sleep(.04)\n"
    if new_tail not in block:
        if old_hardened_tail in block:
            block = block.replace(old_hardened_tail, new_tail, 1)
        elif old_tail in block:
            block = block.replace(old_tail, new_tail, 1)
        else:
            raise SystemExit("Cannot locate Seoul support stop block")
    return block


s = transform_between(s, "def scrape_seoul_office", "def dedupe_merge", harden_seoul)

support_block = s[s.find("def scrape_mircms_board"):s.find("def dedupe_merge")]
for forbidden in ("range(1, 8)", "range(1, 10)", "range(1, 61)", "range(1, FAST_MAX_PAGES + 1)"):
    if forbidden in support_block:
        raise SystemExit(f"Fixed support-office page ceiling remains: {forbidden}")
for required in ('"srchEcptDl":""', "while True:", "fully_dated =", "consecutive_old_pages >= 2", "page += 1"):
    if required not in s:
        raise SystemExit(f"Fast completeness hardening missing: {required}")

p.write_text(s, encoding="utf-8")

# Run the remaining hardeners after the population/pagination transform.
for script in ("harden_central_pagination.py", "harden_identity_dedupe.py", "harden_status_semantics.py"):
    runpy.run_path(str(ROOT / "scripts" / script), run_name="__main__")

print("Fast population, evidence-driven support pagination, central pagination, identity, and status semantics hardened")
