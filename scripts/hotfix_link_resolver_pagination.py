#!/usr/bin/env python3
"""Remove the legacy 8-page cap from the Gyeonggi exact-link resolver.

The resolver must follow the official board until it is exhausted or pagination repeats.
A 500-page ceiling is emergency-only and is reported as unhealthy if reached.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/resolve_support_links.py"
s = p.read_text(encoding="utf-8")

replacements = [
    (
        "        found = 0\n        methods = {}\n        for page in range(1, 9):",
        "        found = 0\n        methods = {}\n        seen_page_sigs = set()\n        pages_scanned = 0\n        stop_reason = 'ceiling'\n        for page in range(1, 501):",
    ),
    (
        "            soup = BeautifulSoup(r.text, \"html.parser\")\n            page_rows = 0",
        "            soup = BeautifulSoup(r.text, \"html.parser\")\n            page_rows = 0\n            page_ids = []\n            candidate_rows = 0",
    ),
    (
        "                    title = title_from_row(tr, headers)\n                    if len(title) < 3:\n                        continue\n                    sn, detail, how = extract_ntt_sn(r.url, tr)",
        "                    title = title_from_row(tr, headers)\n                    if len(title) < 3:\n                        continue\n                    candidate_rows += 1\n                    sn, detail, how = extract_ntt_sn(r.url, tr)",
    ),
    (
        "                    page_rows += 1\n                    found += 1",
        "                    page_rows += 1\n                    page_ids.append(sn)\n                    found += 1",
    ),
    (
        "            if page_rows == 0:\n                break\n        board_stats.append({\"board\": board, \"resolvedRows\": found, \"methods\": methods})",
        "            pages_scanned += 1\n            sig = tuple(page_ids)\n            if sig and sig in seen_page_sigs:\n                stop_reason = 'repeated-page'\n                break\n            if sig:\n                seen_page_sigs.add(sig)\n            if candidate_rows > 0 and page_rows == 0:\n                stop_reason = 'unresolved-row-structure'\n                break\n            if page_rows == 0:\n                stop_reason = 'empty-page'\n                break\n        board_stats.append({\"board\": board, \"resolvedRows\": found, \"methods\": methods, \"pagesScanned\": pages_scanned, \"stopReason\": stop_reason, \"ceilingHit\": pages_scanned >= 500})",
    ),
]

changed = 0
for old, new in replacements:
    if old in s:
        s = s.replace(old, new, 1)
        changed += 1
    elif new not in s:
        raise SystemExit(f"Unexpected resolver source; patch marker missing: {old[:80]}")

bad = "for page in range(1, 9):"
if bad in s:
    raise SystemExit("Legacy 8-page exact-link cap still present")
for marker in ("for page in range(1, 501):", "seen_page_sigs", "unresolved-row-structure", "ceilingHit"):
    if marker not in s:
        raise SystemExit(f"Required resolver pagination marker missing: {marker}")

p.write_text(s, encoding="utf-8")
print(f"Exact-link resolver pagination guard applied ({changed} edits)")
