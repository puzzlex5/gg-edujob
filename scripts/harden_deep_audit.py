#!/usr/bin/env python3
"""Apply all deep-audit crawler hardening in one deterministic, idempotent pass.

This replaces the unsafe workflow pattern of running two independent source rewriters
that expected different versions of complete_support_coverage.py. It first applies the
500-page/structural-end hardener, then adds strict any-page failure semantics, fixes
Seoul GET->POST pagination without skipping the current page, exposes explicit natural
termination evidence, strengthens the completeness verifier, and ensures official-ID
reconciliation uses the full recent 90-day Gyeonggi central population rather than the
UI's active-only `마감제외` view.
"""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
CRAWLER = ROOT / "scripts/complete_support_coverage.py"
VERIFY = ROOT / "scripts/verify_support_completeness.py"

# Phase 1: normalize the original deep crawler to the structural-end/500-page form.
runpy.run_path(str(ROOT / "scripts/harden_complete_crawler.py"), run_name="__main__")

s = CRAWLER.read_text(encoding="utf-8")

# Any page request failure makes the traversal incomplete. The first hardener converts
# the first occurrence (Gyeonggi); this catches Seoul and is harmless if already done.
s = s.replace(
    "access_error = page == 1",
    "access_error = True  # any pagination request failure makes traversal incomplete",
)

# Expose the structural natural-end proof in board metadata for independent verification.
gg_old = '"explicitEmpty": explicit_empty, "crossedLookback": crossed_old,\n        "coverageComplete": complete,'
gg_new = '"explicitEmpty": explicit_empty, "crossedLookback": crossed_old,\n        "naturalEnd": ended_on_structural_empty, "coverageComplete": complete,'
if gg_old in s:
    s = s.replace(gg_old, gg_new, 1)
elif gg_new not in s:
    raise SystemExit("Gyeonggi natural-end metadata marker missing")

seoul_old = '"paginationRepeated": repeat, "accessError": access_error, "explicitEmpty": explicit_empty,\n        "gotTable": got_table, "crossedLookback": crossed_old, "coverageComplete": complete,'
seoul_new = '"paginationRepeated": repeat, "accessError": access_error, "explicitEmpty": explicit_empty,\n        "naturalEnd": ended_on_structural_empty, "gotTable": got_table, "crossedLookback": crossed_old, "coverageComplete": complete,'
if seoul_old in s:
    s = s.replace(seoul_old, seoul_new, 1)
elif seoul_new not in s:
    raise SystemExit("Seoul natural-end metadata marker missing")

# Seoul boards sometimes ignore GET pageIndex but paginate on POST. The legacy fallback
# detected a valid POST page and then continued to the next loop iteration, silently
# dropping that current page. Rebuild and process the POST page immediately instead.
old_fallback = '''        sig = tuple(x[0] for x in items)\n        if sig and sig in seen_page_sigs:\n            if not use_post:\n                # Some Seoul boards ignore GET pageIndex but paginate correctly on POST.\n                use_post = True\n                r2 = seoul_page(board, page, True)\n                if r2:\n                    soup2 = BeautifulSoup(r2.text, "html.parser")\n                    post_items = []\n                    for table in soup2.find_all("table"):\n                        hs = headers(table)\n                        for tr in table.find_all("tr"):\n                            seq = seoul_seq(tr)\n                            if seq:\n                                post_items.append(seq)\n                    post_sig = tuple(post_items)\n                    if post_sig and post_sig not in seen_page_sigs:\n                        continue\n            repeat = True\n            break\n'''
new_fallback = '''        sig = tuple(x[0] for x in items)\n        if sig and sig in seen_page_sigs:\n            if not use_post:\n                # GET can ignore pageIndex. Re-fetch this SAME page with POST and process\n                # it now; continuing would silently skip the current page.\n                use_post = True\n                r2 = seoul_page(board, page, True)\n                if r2:\n                    soup2 = BeautifulSoup(r2.text, "html.parser")\n                    post_items = []\n                    post_dates = []\n                    for table in soup2.find_all("table"):\n                        hs = headers(table)\n                        if not hs:\n                            continue\n                        if not any(any(k in h for k in ("마감", "직종", "학교", "구분", "대상", "분야", "등록일", "작성자")) for h in hs):\n                            continue\n                        got_table = True\n                        for tr in table.find_all("tr"):\n                            if not tr.find_all("td"):\n                                continue\n                            seq = seoul_seq(tr)\n                            if not seq:\n                                continue\n                            vals = row_vals(tr, hs)\n                            anchors = [a for a in tr.find_all("a") if clean(a.get_text(" ", strip=True))]\n                            title = clean(max((a.get_text(" ", strip=True) for a in anchors), key=len, default=""))\n                            if not title:\n                                title = first_of(vals, ["제목", "공고명", "분야1", "분야"])\n                            registered = date_norm(first_of(vals, ["등록일", "작성일"]))\n                            if not registered:\n                                ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                                registered = ds[-1] if ds else ""\n                            post_items.append((seq, title, registered, vals))\n                            if registered:\n                                post_dates.append(registered)\n                    post_sig = tuple(x[0] for x in post_items)\n                    if post_sig and post_sig not in seen_page_sigs:\n                        items, dates, sig = post_items, post_dates, post_sig\n                    else:\n                        repeat = True\n                        break\n                else:\n                    access_error = True\n                    repeat = True\n                    break\n            else:\n                repeat = True\n                break\n'''
if old_fallback in s:
    s = s.replace(old_fallback, new_fallback, 1)
elif "silently skip the current page" not in s:
    raise SystemExit("Seoul GET->POST fallback marker missing")

for marker in (
    "MAX_PAGES = 500",
    "ended_on_structural_empty",
    "naturalEnd",
    "silently skip the current page",
    "any pagination request failure makes traversal incomplete",
):
    if marker not in s:
        raise SystemExit(f"Deep audit hardening incomplete: {marker}")
CRAWLER.write_text(s, encoding="utf-8")

v = VERIFY.read_text(encoding="utf-8")
old = '''            if board.get("accessError"):\n                problems.append(f"{province}/{name}: board traversal stopped on access error")\n            if raw in {70, 80, 90, 100}:\n'''
new = '''            if board.get("accessError"):\n                problems.append(f"{province}/{name}: board traversal stopped on access error before a proven boundary")\n            if not (board.get("crossedLookback") or board.get("naturalEnd") or board.get("explicitEmpty")):\n                problems.append(f"{province}/{name}: no explicit termination proof (90-day boundary / natural end / official empty)")\n            if raw in {70, 80, 90, 100}:\n'''
if old in v:
    v = v.replace(old, new, 1)
elif "no explicit termination proof" not in v:
    raise SystemExit("Verifier termination-proof marker missing")
VERIFY.write_text(v, encoding="utf-8")

# Phase 2: preserve strict traversal rules while adding a narrowly-scoped fallback for
# MirCMS rows whose canonical nttSn exists only in row-level javascript/data attributes.
runpy.run_path(str(ROOT / "scripts/patch_goegn_row_parser.py"), run_name="__main__")

# Phase 3: completeness reconciliation must not inherit Gyeonggi central's active-only
# `마감제외` UI filter. Patch reconciliation to use the dedicated recent-90-day crawler.
runpy.run_path(str(ROOT / "scripts/harden_reconciliation_central_lookback.py"), run_name="__main__")

print("Consolidated deep-audit hardening ready")
