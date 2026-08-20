#!/usr/bin/env python3
"""Idempotently make deep support-office coverage proof conservative.

A crawl is complete only when it reaches the 90-day boundary, proves a natural end,
or the official board explicitly reports zero rows. Any request failure at any page is
incomplete. Also fixes Seoul GET->POST pagination fallback so the current POST page is
processed instead of skipped.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRAWLER = ROOT / "scripts/complete_support_coverage.py"
VERIFY = ROOT / "scripts/verify_support_completeness.py"

s = CRAWLER.read_text(encoding="utf-8")

# Gyeonggi: retain proof of any-page request failure and natural end.
s = s.replace(
    "    access_error = False\n    explicit_empty = False\n    crossed_old = False\n    consecutive_old_pages = 0\n\n    for page in range(1, MAX_PAGES + 1):\n        r = get(with_query(board, currPage=page))\n        if not r:\n            access_error = page == 1\n            break\n",
    "    access_error = False\n    request_error = False\n    explicit_empty = False\n    natural_end = False\n    crossed_old = False\n    consecutive_old_pages = 0\n\n    for page in range(1, MAX_PAGES + 1):\n        r = get(with_query(board, currPage=page))\n        if not r:\n            request_error = True\n            access_error = page == 1\n            break\n",
    1,
)
s = s.replace(
    "        pages += 1\n        if not page_items:\n            break\n",
    "        pages += 1\n        if not page_items:\n            # A later empty page proves pagination exhausted. Page 1 is only a\n            # proven empty board when the official response explicitly says so.\n            natural_end = page > 1 or explicit_empty\n            break\n",
    1,
)
s = s.replace(
    "    complete = (not access_error) and (crossed_old or (pages > 0 and not repeat and pages < MAX_PAGES))\n    return out, {\n        \"url\": board, \"pagesScanned\": pages, \"rawRows\": raw_rows,\n        \"recentRows\": len(out), \"paginationRepeated\": repeat, \"accessError\": access_error,\n        \"explicitEmpty\": explicit_empty, \"crossedLookback\": crossed_old,\n        \"coverageComplete\": complete,\n    }\n",
    "    complete = (not request_error) and (not repeat) and (crossed_old or natural_end or (explicit_empty and pages == 1))\n    return out, {\n        \"url\": board, \"pagesScanned\": pages, \"rawRows\": raw_rows,\n        \"recentRows\": len(out), \"paginationRepeated\": repeat, \"accessError\": access_error,\n        \"requestError\": request_error, \"naturalEnd\": natural_end,\n        \"explicitEmpty\": explicit_empty, \"crossedLookback\": crossed_old,\n        \"coverageComplete\": complete,\n    }\n",
    1,
)

# Seoul: same strict termination semantics.
s = s.replace(
    "    access_error = False\n    explicit_empty = False\n    got_table = False\n    crossed_old = False\n    consecutive_old_pages = 0\n    use_post = False\n",
    "    access_error = False\n    request_error = False\n    explicit_empty = False\n    natural_end = False\n    got_table = False\n    crossed_old = False\n    consecutive_old_pages = 0\n    use_post = False\n",
    1,
)
s = s.replace(
    "        if not r:\n            access_error = page == 1\n            break\n        soup = BeautifulSoup(r.text, \"html.parser\")\n",
    "        if not r:\n            request_error = True\n            access_error = page == 1\n            break\n        soup = BeautifulSoup(r.text, \"html.parser\")\n",
    1,
)

old_fallback = '''        sig = tuple(x[0] for x in items)\n        if sig and sig in seen_page_sigs:\n            if not use_post:\n                # Some Seoul boards ignore GET pageIndex but paginate correctly on POST.\n                use_post = True\n                r2 = seoul_page(board, page, True)\n                if r2:\n                    soup2 = BeautifulSoup(r2.text, \"html.parser\")\n                    post_items = []\n                    for table in soup2.find_all(\"table\"):\n                        hs = headers(table)\n                        for tr in table.find_all(\"tr\"):\n                            seq = seoul_seq(tr)\n                            if seq:\n                                post_items.append(seq)\n                    post_sig = tuple(post_items)\n                    if post_sig and post_sig not in seen_page_sigs:\n                        continue\n            repeat = True\n            break\n'''
new_fallback = '''        sig = tuple(x[0] for x in items)\n        if sig and sig in seen_page_sigs:\n            if not use_post:\n                # Some Seoul boards ignore GET pageIndex but paginate correctly on POST.\n                # Re-fetch *this same page* with POST and process it now; skipping to the\n                # next loop iteration would silently lose the current page.\n                use_post = True\n                r2 = seoul_page(board, page, True)\n                if r2:\n                    soup2 = BeautifulSoup(r2.text, \"html.parser\")\n                    post_items = []\n                    post_dates = []\n                    for table in soup2.find_all(\"table\"):\n                        hs = headers(table)\n                        if not hs:\n                            continue\n                        if not any(any(k in h for k in (\"마감\", \"직종\", \"학교\", \"구분\", \"대상\", \"분야\", \"등록일\", \"작성자\")) for h in hs):\n                            continue\n                        got_table = True\n                        for tr in table.find_all(\"tr\"):\n                            if not tr.find_all(\"td\"):\n                                continue\n                            seq = seoul_seq(tr)\n                            if not seq:\n                                continue\n                            vals = row_vals(tr, hs)\n                            anchors = [a for a in tr.find_all(\"a\") if clean(a.get_text(\" \", strip=True))]\n                            title = clean(max((a.get_text(\" \", strip=True) for a in anchors), key=len, default=\"\"))\n                            if not title:\n                                title = first_of(vals, [\"제목\", \"공고명\", \"분야1\", \"분야\"])\n                            registered = date_norm(first_of(vals, [\"등록일\", \"작성일\"]))\n                            if not registered:\n                                ds = all_dates(clean(tr.get_text(\" \", strip=True)))\n                                registered = ds[-1] if ds else \"\"\n                            post_items.append((seq, title, registered, vals))\n                            if registered:\n                                post_dates.append(registered)\n                    post_sig = tuple(x[0] for x in post_items)\n                    if post_sig and post_sig not in seen_page_sigs:\n                        items, dates, sig = post_items, post_dates, post_sig\n                    else:\n                        repeat = True\n                        break\n                else:\n                    request_error = True\n                    repeat = True\n                    break\n            else:\n                repeat = True\n                break\n'''
if old_fallback in s:
    s = s.replace(old_fallback, new_fallback, 1)
elif "silently lose the current page" not in s:
    raise SystemExit("Seoul pagination fallback marker not found")

# The second occurrence is Seoul's empty-page handling.
needle = "        pages += 1\n        if not items:\n            break\n        raw_rows += len(items)\n"
if needle in s:
    s = s.replace(
        needle,
        "        pages += 1\n        if not items:\n            natural_end = page > 1 or explicit_empty\n            break\n        raw_rows += len(items)\n",
        1,
    )
elif "natural_end = page > 1 or explicit_empty" not in s:
    raise SystemExit("Seoul empty-page marker not found")

s = s.replace(
    "    complete = (not access_error) and (crossed_old or (pages > 0 and not repeat and pages < MAX_PAGES))\n    return out, {\n        \"url\": board, \"pagesScanned\": pages, \"rawRows\": raw_rows, \"recentRows\": len(out),\n        \"paginationRepeated\": repeat, \"accessError\": access_error, \"explicitEmpty\": explicit_empty,\n        \"gotTable\": got_table, \"crossedLookback\": crossed_old, \"coverageComplete\": complete,\n        \"paginationMethod\": \"POST\" if use_post else \"GET\",\n    }\n",
    "    complete = (not request_error) and (not repeat) and (crossed_old or natural_end or (explicit_empty and pages == 1))\n    return out, {\n        \"url\": board, \"pagesScanned\": pages, \"rawRows\": raw_rows, \"recentRows\": len(out),\n        \"paginationRepeated\": repeat, \"accessError\": access_error, \"requestError\": request_error,\n        \"naturalEnd\": natural_end, \"explicitEmpty\": explicit_empty,\n        \"gotTable\": got_table, \"crossedLookback\": crossed_old, \"coverageComplete\": complete,\n        \"paginationMethod\": \"POST\" if use_post else \"GET\",\n    }\n",
    1,
)

if "requestError\"" not in s or "silently lose the current page" not in s:
    raise SystemExit("Completeness hardening did not apply")
CRAWLER.write_text(s, encoding="utf-8")

v = VERIFY.read_text(encoding="utf-8")
old = '''            if board.get("accessError"):\n                problems.append(f"{province}/{name}: board traversal stopped on access error")\n            if raw in {70, 80, 90, 100}:\n'''
new = '''            if board.get("accessError"):\n                problems.append(f"{province}/{name}: board traversal stopped on first-page access error")\n            if board.get("requestError"):\n                problems.append(f"{province}/{name}: board traversal stopped on request error before a proven boundary")\n            if not (board.get("crossedLookback") or board.get("naturalEnd") or board.get("explicitEmpty")):\n                problems.append(f"{province}/{name}: no explicit termination proof (lookback boundary / natural end / official empty)")\n            if raw in {70, 80, 90, 100}:\n'''
if old in v:
    v = v.replace(old, new, 1)
elif "no explicit termination proof" not in v:
    raise SystemExit("Verifier marker not found")
VERIFY.write_text(v, encoding="utf-8")

print("strict completeness semantics ready")
