#!/usr/bin/env python3
"""Idempotently align the fast crawl with the complete recent recruitment population.

Support-office traversal is evidence-driven: stop only on no new semantic rows/repeated pages,
or after two consecutive *fully dated* pages are entirely older than the retention boundary.
Numeric support-page ceilings must never control traversal, including second-pass repair checks.
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

# The Gyeonggi second-pass repair/health checker used to stop after exactly seven pages.
# Harden it too, because its status is used as evidence and must never turn a 70-row cap into green health.
rp = ROOT / "scripts/repair_gyeonggi_support.py"
rs = rp.read_text(encoding="utf-8")

if "for page in range(1, 8):" in rs:
    rs = rs.replace(
        "    seen = set()\n\n    for page in range(1, 8):\n",
        "    seen = set()\n    seen_page_signatures = set()\n    consecutive_old_pages = 0\n    stop_reason = \"\"\n    late_error = \"\"\n    page = 1\n\n    while True:\n",
        1,
    )

if "seen_page_signatures = set()" not in rs:
    raise SystemExit("Cannot harden Gyeonggi repair fixed-page loop")

rs = rs.replace(
    "            if page == 1:\n                return collected, {\"url\": board_url, \"rawRows\": 0, \"pagesOk\": 0, \"explicitEmpty\": False, \"error\": f\"{type(exc).__name__}: {str(exc)[:100]}\"}\n            break\n",
    "            if page == 1:\n                return collected, {\"url\": board_url, \"rawRows\": 0, \"pagesOk\": 0, \"explicitEmpty\": False, \"error\": f\"{type(exc).__name__}: {str(exc)[:100]}\", \"stopReason\": \"first_page_error\", \"crossedLookback\": False}\n            late_error = f\"{type(exc).__name__}: {str(exc)[:100]}\"\n            stop_reason = \"later_page_error\"\n            break\n",
    1,
)

rs = rs.replace(
    "        page_table_rows = 0\n        page_recent_rows = 0\n",
    "        page_table_rows = 0\n        page_recent_rows = 0\n        page_dates = []\n        page_row_keys = []\n",
    1,
)

needle = "                if not registered:\n                    dates = [date_norm(m.group(0)) for m in DATE_RE.finditer(row_text)]\n                    registered = dates[-1] if dates else \"\"\n\n                if registered and not recent_enough(registered):\n"
replacement = "                if not registered:\n                    dates = [date_norm(m.group(0)) for m in DATE_RE.finditer(row_text)]\n                    registered = dates[-1] if dates else \"\"\n                page_row_keys.append((norm(title), registered))\n                if registered:\n                    page_dates.append(registered)\n\n                if registered and not recent_enough(registered):\n"
if replacement not in rs:
    if needle not in rs:
        raise SystemExit("Cannot locate Gyeonggi repair registration-date block")
    rs = rs.replace(needle, replacement, 1)

old_tail = "        if page_table_rows == 0:\n            break\n        # Once rows are older than the recent window there is no need to crawl deeper.\n        if page_recent_rows == 0 and page >= 2:\n            break\n\n    return collected, {\"url\": board_url, \"rawRows\": raw_rows, \"pagesOk\": pages_ok, \"explicitEmpty\": explicit_empty, \"error\": \"\"}\n"
new_tail = "        if page_table_rows == 0:\n            stop_reason = \"no_rows\"\n            break\n        signature = tuple(page_row_keys)\n        if signature and signature in seen_page_signatures:\n            stop_reason = \"repeated_page\"\n            break\n        if signature:\n            seen_page_signatures.add(signature)\n        fully_dated = len(page_dates) == page_table_rows\n        if fully_dated and page_dates and all(not recent_enough(d) for d in page_dates):\n            consecutive_old_pages += 1\n        else:\n            consecutive_old_pages = 0\n        if consecutive_old_pages >= 2:\n            stop_reason = \"retention_boundary\"\n            break\n        page += 1\n\n    return collected, {\"url\": board_url, \"rawRows\": raw_rows, \"pagesOk\": pages_ok, \"explicitEmpty\": explicit_empty, \"error\": late_error, \"stopReason\": stop_reason, \"crossedLookback\": stop_reason == \"retention_boundary\", \"lastPage\": pages_ok}\n"
if new_tail not in rs:
    if old_tail not in rs:
        raise SystemExit("Cannot locate Gyeonggi repair stop block")
    rs = rs.replace(old_tail, new_tail, 1)

old_health = "        errors = [m.get(\"error\") for m in board_meta if m.get(\"error\")]\n\n        if not boards:\n            state, ok, message = \"error\", False, \"실제 채용 게시판 URL 미등록\"\n        elif raw_total > 0:\n            state, ok, message = \"ok\", True, f\"실제 게시판 행 {raw_total}건 확인 · 최근 채용 {len(rows)}건\"\n"
new_health = "        errors = [m.get(\"error\") for m in board_meta if m.get(\"error\")]\n        safe_stops = {\"no_rows\", \"repeated_page\", \"retention_boundary\"}\n        traversal_complete = bool(board_meta) and all((not m.get(\"error\")) and m.get(\"stopReason\") in safe_stops for m in board_meta)\n\n        if not boards:\n            state, ok, message = \"error\", False, \"실제 채용 게시판 URL 미등록\"\n        elif raw_total > 0 and traversal_complete:\n            state, ok, message = \"ok\", True, f\"실제 게시판 행 {raw_total}건 완전순회 · 최근 채용 {len(rows)}건\"\n        elif raw_total > 0:\n            state, ok, message = \"needs_check\", False, f\"게시판 행 {raw_total}건 확인했으나 pagination 종료 근거 불충분\"\n"
if new_health not in rs:
    if old_health not in rs:
        raise SystemExit("Cannot locate Gyeonggi repair health classification")
    rs = rs.replace(old_health, new_health, 1)

repair_block = rs[rs.find("def fetch_board"):rs.find("def merge_jobs")]
if "for page in range(1, 8)" in repair_block:
    raise SystemExit("Fixed seven-page Gyeonggi repair ceiling remains")
for required in ("while True:", "seen_page_signatures", "fully_dated =", "consecutive_old_pages >= 2", 'stop_reason = "retention_boundary"'):
    if required not in repair_block:
        raise SystemExit(f"Gyeonggi repair completeness hardening missing: {required}")
if "traversal_complete" not in rs:
    raise SystemExit("Gyeonggi repair health still lacks traversal-completeness gate")

rp.write_text(rs, encoding="utf-8")

# Run the remaining hardeners after the population/pagination transforms.
for script in ("harden_central_pagination.py", "harden_identity_dedupe.py", "harden_status_semantics.py"):
    runpy.run_path(str(ROOT / "scripts" / script), run_name="__main__")

print("Fast population, evidence-driven support pagination, second-pass completeness, central pagination, identity, and status semantics hardened")
