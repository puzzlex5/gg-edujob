#!/usr/bin/env python3
"""Make the 90-day completeness crawler strict/deep without inflating fast collector page caps."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/complete_support_coverage.py"
s = p.read_text(encoding="utf-8")

replacements = [
    ("MAX_PAGES = 80", "MAX_PAGES = 500  # emergency ceiling; normal stop is lookback/structural end"),
    ("access_error = page == 1", "access_error = True  # any pagination request failure makes traversal incomplete"),
    ("    consecutive_old_pages = 0\n\n    for page in range(1, MAX_PAGES + 1):",
     "    consecutive_old_pages = 0\n    ended_on_structural_empty = False\n\n    for page in range(1, MAX_PAGES + 1):"),
    ("        page_items = []\n        page_dates = []\n        for table in soup.find_all(\"table\"):\n            hs = headers(table)",
     "        page_items = []\n        page_dates = []\n        page_has_expected_table = False\n        page_candidate_rows = 0\n        for table in soup.find_all(\"table\"):\n            hs = headers(table)\n            table_is_expected = any(any(k in h for k in (\"제목\", \"학교\", \"기관\", \"등록일\", \"작성일\", \"마감\")) for h in hs)\n            if table_is_expected:\n                page_has_expected_table = True"),
    ("                if not tds:\n                    continue\n                pick = None",
     "                if not tds:\n                    continue\n                if table_is_expected:\n                    page_candidate_rows += 1\n                pick = None"),
    ("        pages += 1\n        if not page_items:\n            break\n\n        raw_rows += len(page_items)",
     "        pages += 1\n        if not page_items:\n            ended_on_structural_empty = bool(explicit_empty or (page_has_expected_table and page_candidate_rows == 0))\n            break\n\n        raw_rows += len(page_items)"),
    ("    complete = (not access_error) and (crossed_old or (pages > 0 and not repeat and pages < MAX_PAGES))\n    return out, {\n        \"url\": board, \"pagesScanned\": pages, \"rawRows\": raw_rows,",
     "    complete = (not access_error) and (pages < MAX_PAGES) and (crossed_old or ended_on_structural_empty)\n    return out, {\n        \"url\": board, \"pagesScanned\": pages, \"rawRows\": raw_rows,"),
    ("    use_post = False\n\n    for page in range(1, MAX_PAGES + 1):",
     "    use_post = False\n    ended_on_structural_empty = False\n\n    for page in range(1, MAX_PAGES + 1):"),
    ("        items = []\n        dates = []\n        for table in soup.find_all(\"table\"):\n            hs = headers(table)",
     "        items = []\n        dates = []\n        page_has_expected_table = False\n        page_candidate_rows = 0\n        for table in soup.find_all(\"table\"):\n            hs = headers(table)"),
    ("            got_table = True\n            for tr in table.find_all(\"tr\"):\n                if not tr.find_all(\"td\"):\n                    continue",
     "            got_table = True\n            page_has_expected_table = True\n            for tr in table.find_all(\"tr\"):\n                if not tr.find_all(\"td\"):\n                    continue\n                page_candidate_rows += 1"),
    ("        pages += 1\n        if not items:\n            break\n        raw_rows += len(items)",
     "        pages += 1\n        if not items:\n            ended_on_structural_empty = bool(explicit_empty or (page_has_expected_table and page_candidate_rows == 0))\n            break\n        raw_rows += len(items)"),
    ("    complete = (not access_error) and (crossed_old or (pages > 0 and not repeat and pages < MAX_PAGES))\n    return out, {\n        \"url\": board, \"pagesScanned\": pages, \"rawRows\": raw_rows, \"recentRows\": len(out),",
     "    complete = (not access_error) and (pages < MAX_PAGES) and (crossed_old or ended_on_structural_empty)\n    return out, {\n        \"url\": board, \"pagesScanned\": pages, \"rawRows\": raw_rows, \"recentRows\": len(out),"),
]

changed = 0
for old, new in replacements:
    if old in s:
        s = s.replace(old, new, 1)
        changed += 1
    elif new not in s:
        raise SystemExit(f"Unexpected completeness source; marker missing: {old[:100]}")

for marker in ("MAX_PAGES = 500", "ended_on_structural_empty", "page_candidate_rows", "access_error = True"):
    if marker not in s:
        raise SystemExit(f"Required completeness marker missing: {marker}")

p.write_text(s, encoding="utf-8")
print(f"Deep completeness crawler hardened ({changed} edits); fast collector caps untouched")
