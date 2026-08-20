#!/usr/bin/env python3
"""Harden Gyeonggi exact-link resolution against truncation and wrong-title remapping."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/resolve_support_links.py"
s = p.read_text(encoding="utf-8")

replacements = [
    (
        "        found = 0\n        methods = {}\n        for page in range(1, 9):",
        "        found = 0\n        methods = {}\n        seen_page_sigs = set()\n        pages_scanned = 0\n        unresolved_candidates = 0\n        stop_reason = 'ceiling'\n        for page in range(1, 501):",
    ),
    (
        "            r = get(with_page(board, page))\n            if not r:\n                break\n            soup = BeautifulSoup(r.text, \"html.parser\")\n            page_rows = 0",
        "            r = get(with_page(board, page))\n            if not r:\n                stop_reason = 'access-error'\n                break\n            soup = BeautifulSoup(r.text, \"html.parser\")\n            page_rows = 0\n            page_ids = []\n            candidate_rows = 0",
    ),
    (
        "                    title = title_from_row(tr, headers)\n                    if len(title) < 3:\n                        continue\n                    sn, detail, how = extract_ntt_sn(r.url, tr)",
        "                    title = title_from_row(tr, headers)\n                    if len(title) < 3:\n                        continue\n                    candidate_rows += 1\n                    sn, detail, how = extract_ntt_sn(r.url, tr)",
    ),
    (
        "                    if not sn or not detail:\n                        continue\n                    page_rows += 1\n                    found += 1",
        "                    if not sn or not detail:\n                        unresolved_candidates += 1\n                        continue\n                    page_rows += 1\n                    page_ids.append(sn)\n                    found += 1",
    ),
    (
        "            if page_rows == 0:\n                break\n        board_stats.append({\"board\": board, \"resolvedRows\": found, \"methods\": methods})",
        "            pages_scanned += 1\n            sig = tuple(page_ids)\n            if sig and sig in seen_page_sigs:\n                stop_reason = 'repeated-page'\n                break\n            if sig:\n                seen_page_sigs.add(sig)\n            if candidate_rows > 0 and page_rows == 0:\n                stop_reason = 'unresolved-row-structure'\n                break\n            if page_rows == 0:\n                stop_reason = 'empty-page'\n                break\n        board_stats.append({\"board\": board, \"resolvedRows\": found, \"methods\": methods, \"pagesScanned\": pages_scanned, \"stopReason\": stop_reason, \"ceilingHit\": pages_scanned >= 500, \"unresolvedCandidates\": unresolved_candidates})",
    ),
    (
        "            source_names = job.get(\"checkedSources\") or [job.get(\"source\", \"\")]\n            hit = None\n            for office in source_names:\n                hit = all_map.get((office, norm(job.get(\"title\", \"\"))))\n                if hit:\n                    break\n            if not hit:\n                hit = all_map.get((job.get(\"source\", \"\"), norm(job.get(\"title\", \"\"))))\n            if hit:\n                job.update({\n                    \"url\": hit[\"url\"],\n                    \"boardUrl\": hit[\"boardUrl\"],\n                    \"detailLinkResolved\": True,\n                    \"nttSn\": hit[\"nttSn\"],\n                    \"bbsId\": hit[\"bbsId\"],\n                    \"mi\": hit[\"mi\"],\n                    \"detailResolutionMethod\": hit[\"method\"],\n                })\n                gyeonggi_resolved += 1\n            elif job.get(\"url\") and \"selectNttInfo.do\" in job.get(\"url\", \"\"):\n                q = parse_qs(urlparse(job[\"url\"]).query)\n                sn = (q.get(\"nttSn\") or [\"\"])[0]\n                bbs = (q.get(\"bbsId\") or [\"\"])[0]\n                if sn.isdigit() and bbs:\n                    job[\"detailLinkResolved\"] = True\n                    job[\"nttSn\"] = sn\n                    job[\"bbsId\"] = bbs\n                    job[\"mi\"] = (q.get(\"mi\") or [\"\"])[0]\n                    gyeonggi_resolved += 1",
        "            # Preserve an already canonical individual URL. Title-only matching is ambiguous\n            # when an office publishes the same generic title repeatedly.\n            current_exact = False\n            if job.get(\"url\") and \"selectNttInfo.do\" in job.get(\"url\", \"\"):\n                q = parse_qs(urlparse(job[\"url\"]).query)\n                sn = (q.get(\"nttSn\") or [\"\"])[0]\n                bbs = (q.get(\"bbsId\") or [\"\"])[0]\n                if sn.isdigit() and bbs:\n                    job[\"detailLinkResolved\"] = True\n                    job[\"nttSn\"] = sn\n                    job[\"bbsId\"] = bbs\n                    job[\"mi\"] = (q.get(\"mi\") or [\"\"])[0]\n                    gyeonggi_resolved += 1\n                    current_exact = True\n            if not current_exact:\n                source_names = job.get(\"checkedSources\") or [job.get(\"source\", \"\")]\n                hit = None\n                for office in source_names:\n                    hit = all_map.get((office, norm(job.get(\"title\", \"\"))))\n                    if hit:\n                        break\n                if not hit:\n                    hit = all_map.get((job.get(\"source\", \"\"), norm(job.get(\"title\", \"\"))))\n                if hit:\n                    job.update({\n                        \"url\": hit[\"url\"], \"boardUrl\": hit[\"boardUrl\"],\n                        \"detailLinkResolved\": True, \"nttSn\": hit[\"nttSn\"],\n                        \"bbsId\": hit[\"bbsId\"], \"mi\": hit[\"mi\"],\n                        \"detailResolutionMethod\": hit[\"method\"],\n                    })\n                    gyeonggi_resolved += 1",
    ),
]

changed = 0
for old, new in replacements:
    if old in s:
        s = s.replace(old, new, 1)
        changed += 1
    elif new not in s:
        raise SystemExit(f"Unexpected resolver source; patch marker missing: {old[:100]}")

for bad in ("for page in range(1, 9):", "if hit:\n                job.update({"):
    if bad in s:
        raise SystemExit(f"Unsafe exact-link resolver behavior remains: {bad}")
for marker in ("for page in range(1, 501):", "seen_page_sigs", "stop_reason = 'access-error'", "unresolvedCandidates", "current_exact = False"):
    if marker not in s:
        raise SystemExit(f"Required resolver safety marker missing: {marker}")

p.write_text(s, encoding="utf-8")
print(f"Exact-link resolver safety patch applied ({changed} edits)")
