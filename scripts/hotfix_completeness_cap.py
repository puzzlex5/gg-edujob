#!/usr/bin/env python3
"""Convert low legacy page caps into emergency-only safety ceilings.

Collectors must normally stop because the official board is exhausted, repeats, or the
retention window is safely crossed—not because an arbitrary small page number was reached.
A finite 500-page ceiling remains only as protection against a broken pagination endpoint.
The completeness crawler treats hitting that ceiling as incomplete/unhealthy.

Any request failure during pagination is also incomplete. A successful HTTP response with
no parsable rows is not enough by itself: the crawler must see the expected board/table
structure (or an explicit empty-board message) before treating that page as a normal end.
This prevents a layout change, login/error shell, or soft failure from being misclassified
as complete coverage.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch(path, replacements, required=()):
    p = ROOT / path
    s = p.read_text(encoding="utf-8")
    changed = 0
    for old, new in replacements:
        if old in s:
            s = s.replace(old, new)
            changed += 1
        elif new not in s:
            raise SystemExit(f"Unexpected pagination marker in {path}: {old}")
    for marker in required:
        if marker not in s:
            raise SystemExit(f"Required safety marker missing in {path}: {marker}")
    p.write_text(s, encoding="utf-8")
    return changed


changed = 0
changed += patch(
    "scripts/complete_support_coverage.py",
    [
        ("MAX_PAGES = 80", "MAX_PAGES = 500  # emergency infinite-loop ceiling; reaching it is NOT complete"),
        ("access_error = page == 1", "access_error = True  # any mid-pagination request failure makes traversal incomplete"),
        ("        page_items = []\n        page_dates = []\n        for table in soup.find_all(\"table\"):\n            hs = headers(table)",
         "        page_items = []\n        page_dates = []\n        page_has_expected_table = False\n        for table in soup.find_all(\"table\"):\n            hs = headers(table)\n            if any(any(k in h for k in (\"제목\", \"학교\", \"기관\", \"등록일\", \"작성일\", \"마감\")) for h in hs):\n                page_has_expected_table = True"),
        ("        pages += 1\n        if not page_items:\n            break\n\n        raw_rows += len(page_items)",
         "        pages += 1\n        if not page_items:\n            ended_on_structural_empty = bool(explicit_empty or page_has_expected_table)\n            break\n\n        raw_rows += len(page_items)"),
        ("    consecutive_old_pages = 0\n\n    for page in range(1, MAX_PAGES + 1):",
         "    consecutive_old_pages = 0\n    ended_on_structural_empty = False\n\n    for page in range(1, MAX_PAGES + 1):"),
        ("    complete = (not access_error) and (crossed_old or (pages > 0 and not repeat and pages < MAX_PAGES))\n    return out, {\n        \"url\": board, \"pagesScanned\": pages, \"rawRows\": raw_rows,",
         "    complete = (not access_error) and (pages < MAX_PAGES) and (crossed_old or ended_on_structural_empty)\n    return out, {\n        \"url\": board, \"pagesScanned\": pages, \"rawRows\": raw_rows,"),
        ("    use_post = False\n\n    for page in range(1, MAX_PAGES + 1):",
         "    use_post = False\n    ended_on_structural_empty = False\n\n    for page in range(1, MAX_PAGES + 1):"),
        ("        items = []\n        dates = []\n        for table in soup.find_all(\"table\"):\n            hs = headers(table)",
         "        items = []\n        dates = []\n        page_has_expected_table = False\n        for table in soup.find_all(\"table\"):\n            hs = headers(table)"),
        ("            got_table = True\n            for tr in table.find_all(\"tr\"):",
         "            got_table = True\n            page_has_expected_table = True\n            for tr in table.find_all(\"tr\"):"),
        ("        pages += 1\n        if not items:\n            break\n        raw_rows += len(items)",
         "        pages += 1\n        if not items:\n            ended_on_structural_empty = bool(explicit_empty or page_has_expected_table)\n            break\n        raw_rows += len(items)"),
        ("    complete = (not access_error) and (crossed_old or (pages > 0 and not repeat and pages < MAX_PAGES))\n    return out, {\n        \"url\": board, \"pagesScanned\": pages, \"rawRows\": raw_rows, \"recentRows\": len(out),",
         "    complete = (not access_error) and (pages < MAX_PAGES) and (crossed_old or ended_on_structural_empty)\n    return out, {\n        \"url\": board, \"pagesScanned\": pages, \"rawRows\": raw_rows, \"recentRows\": len(out),"),
    ],
    required=(
        "pages < MAX_PAGES",
        "access_error = True  # any mid-pagination request failure makes traversal incomplete",
        "ended_on_structural_empty",
        "page_has_expected_table",
    ),
)
# The primary collectors already stop on empty/repeated/no-new rows. Raise only their low
# artificial ceilings so those content-based stop conditions, rather than page number, govern.
changed += patch(
    "scripts/scrape_jobs.py",
    [
        ("for page in range(1, 35):", "for page in range(1, 501):"),
        ("for page in range(1, 8):", "for page in range(1, 501):"),
        ("for page in range(1, 60):", "for page in range(1, 501):"),
        ("for page in range(1, 10):", "for page in range(1, 501):"),
    ],
)
# This repair pass is followed by the stricter completeness crawler. Its low 7-page ceiling
# must not truncate a busy board before the complete pass can reconcile it.
changed += patch(
    "scripts/repair_gyeonggi_support.py",
    [("for page in range(1, 8):", "for page in range(1, 501):")],
)

# Guard against accidental reintroduction of the known low caps, false-complete mid-pagination
# failures, and the old 'HTTP 200 + no parsed rows = complete' behavior.
for rel, bad in {
    "scripts/complete_support_coverage.py": (
        "MAX_PAGES = 80",
        "access_error = page == 1",
        "crossed_old or (pages > 0 and not repeat and pages < MAX_PAGES)",
    ),
    "scripts/scrape_jobs.py": ("range(1, 35)", "range(1, 8)", "range(1, 60)", "range(1, 10)"),
    "scripts/repair_gyeonggi_support.py": ("range(1, 8)",),
}.items():
    text = (ROOT / rel).read_text(encoding="utf-8")
    hits = [x for x in bad if x in text]
    if hits:
        raise SystemExit(f"Unsafe pagination behavior remains in {rel}: {hits}")

print(f"Pagination completeness guard applied ({changed} source groups changed); completeness now requires retention-boundary or structural-empty evidence")
