#!/usr/bin/env python3
"""Idempotently enforce the 90-day retention boundary in the fast Gyeonggi central crawl.

The fast collector intentionally disables the active-only filter so recently closed postings are
not lost. Without an explicit registration-date boundary, however, that turns the fast path into
an all-history crawl. This hardener keeps closed postings while filtering rows older than 90 days
and requires two consecutive fully dated old pages before treating the retention boundary as
crossed. Empty/repeated/final-page evidence remains valid independently.

It also makes the publication verifier retention-aware. A previously over-collected baseline must
not permanently block a corrected 90-day population as a false "central drop" when the corrected
count closely matches the independently reconciled official 90-day count.
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

old_init = '    jobs, seen_ids = [], set()\n    for code, (cat_name, ui_type) in GYEONGGI_CATEGORIES.items():\n'
new_init = '    jobs, seen_ids = [], set()\n    for code, (cat_name, ui_type) in GYEONGGI_CATEGORIES.items():\n        consecutive_old_pages = 0\n'
if new_init not in block:
    if old_init not in block:
        raise SystemExit("Cannot locate Gyeonggi central category initialization")
    block = block.replace(old_init, new_init, 1)

old_added = '            added = 0\n            for li in rows:\n'
new_added = '            added = 0\n            page_dates = []\n            for li in rows:\n'
if new_added not in block:
    if old_added not in block:
        raise SystemExit("Cannot locate Gyeonggi central page initialization")
    block = block.replace(old_added, new_added, 1)

old_registered = '                registered = next((date_norm(x) for x in top if "등록일" in x), "")\n                title_el = li.select_one(".cont_tit")\n'
new_registered = '                registered = next((date_norm(x) for x in top if "등록일" in x), "")\n                if registered: page_dates.append(registered)\n                title_el = li.select_one(".cont_tit")\n'
if new_registered not in block:
    if old_registered not in block:
        raise SystemExit("Cannot locate Gyeonggi central registration date parsing")
    block = block.replace(old_registered, new_registered, 1)

old_append = '                region = find_region(body_text, GYEONGGI_REGIONS)\n                jobs.append({\n'
new_append = '                region = find_region(body_text, GYEONGGI_REGIONS)\n                if registered and not recent_enough(registered, 90):\n                    continue\n                jobs.append({\n'
if new_append not in block:
    if old_append not in block:
        raise SystemExit("Cannot locate Gyeonggi central append point")
    block = block.replace(old_append, new_append, 1)

old_tail = '            if added == 0 or len(rows) < 45: break\n            time.sleep(.05)\n'
new_tail = '''            if added == 0: break
            fully_dated = len(page_dates) == added
            if fully_dated and page_dates and all(not recent_enough(d, 90) for d in page_dates):
                consecutive_old_pages += 1
            else:
                consecutive_old_pages = 0
            if consecutive_old_pages >= 2: break
            if len(rows) < 45: break
            time.sleep(.05)
'''
if new_tail not in block:
    if old_tail not in block:
        raise SystemExit("Cannot locate Gyeonggi central stop block")
    block = block.replace(old_tail, new_tail, 1)

for required in (
    '"srchEcptDl":""',
    "consecutive_old_pages = 0",
    "page_dates = []",
    "if registered: page_dates.append(registered)",
    "if registered and not recent_enough(registered, 90):",
    "fully_dated = len(page_dates) == added",
    "consecutive_old_pages >= 2",
):
    if required not in block:
        raise SystemExit(f"Gyeonggi central 90-day hardening missing: {required}")

s = s[:start] + block + s[end:]
p.write_text(s, encoding="utf-8")

# The verifier normally compares the new central count with the previous publication. That is a
# useful safety guard, except when the previous publication is itself known to be inflated by the
# all-history bug this hardener fixes. Suppress only that specific false positive: the new count
# must closely match an independently reconciled COMPLETE official 90-day source count, while the
# previous count must be materially above that authoritative population.
vp = ROOT / "scripts/verify_jobs.py"
vs = vp.read_text(encoding="utf-8")
old_guard = '''        for key, label in (("gyeonggi", "경기"), ("seoul", "서울")):
            before, after = central_count(prev, key), central_count(data, key)
            if before >= 50 and after < before * 0.35:
                critical.append(f"{label} 중앙 공고 급감: {before}→{after}")
'''
new_guard = '''        recon = load(ROOT / "source_reconciliation_report.json") or {}
        rsummary = recon.get("summary", {}) if isinstance(recon, dict) else {}
        recon_complete = (
            int(rsummary.get("missingAfter") or 0) == 0
            and int(rsummary.get("reconciledSources") or 0) > 0
            and int(rsummary.get("reconciledSources") or 0) == int(rsummary.get("totalSources") or 0)
            and int(rsummary.get("lookbackDays") or recon.get("lookbackDays") or 0) == 90
        )
        authoritative = {}
        if recon_complete:
            for src in recon.get("sources", []):
                name = src.get("name", "")
                if src.get("coverageComplete") is not True:
                    continue
                if name == "경기도교육청 통합 구인구직":
                    authoritative["gyeonggi"] = int(src.get("officialIdCount") or 0)
                elif name == "서울교육일자리포털":
                    authoritative["seoul"] = int(src.get("officialIdCount") or 0)
        for key, label in (("gyeonggi", "경기"), ("seoul", "서울")):
            before, after = central_count(prev, key), central_count(data, key)
            expected = authoritative.get(key, 0)
            verified_retention_normalization = (
                expected >= 50
                and expected * 0.95 <= after <= expected * 1.05
                and before > expected * 1.5
            )
            if before >= 50 and after < before * 0.35 and not verified_retention_normalization:
                critical.append(f"{label} 중앙 공고 급감: {before}→{after}")
'''
if new_guard not in vs:
    if old_guard not in vs:
        raise SystemExit("Cannot locate verifier central-drop guard")
    vs = vs.replace(old_guard, new_guard, 1)
vp.write_text(vs, encoding="utf-8")

print("Gyeonggi central fast crawl constrained to the verified 90-day population; verifier accepts only independently reconciled retention normalization")
