#!/usr/bin/env python3
"""Protect canonical support-office detail links without turning hourly refresh into a full 500-page rescan.

The completeness crawler is responsible for exhaustive board traversal. This patch only prevents
an already exact Gyeonggi detail URL from being overwritten by ambiguous title matching. The
resolver keeps its bounded fallback scan for genuinely unresolved legacy rows.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/resolve_support_links.py"
s = p.read_text(encoding="utf-8")

old = '''            source_names = job.get("checkedSources") or [job.get("source", "")]
            hit = None
            for office in source_names:
                hit = all_map.get((office, norm(job.get("title", ""))))
                if hit:
                    break
            if not hit:
                hit = all_map.get((job.get("source", ""), norm(job.get("title", ""))))
            if hit:
                job.update({
                    "url": hit["url"],
                    "boardUrl": hit["boardUrl"],
                    "detailLinkResolved": True,
                    "nttSn": hit["nttSn"],
                    "bbsId": hit["bbsId"],
                    "mi": hit["mi"],
                    "detailResolutionMethod": hit["method"],
                })
                gyeonggi_resolved += 1
            elif job.get("url") and "selectNttInfo.do" in job.get("url", ""):
                q = parse_qs(urlparse(job["url"]).query)
                sn = (q.get("nttSn") or [""])[0]
                bbs = (q.get("bbsId") or [""])[0]
                if sn.isdigit() and bbs:
                    job["detailLinkResolved"] = True
                    job["nttSn"] = sn
                    job["bbsId"] = bbs
                    job["mi"] = (q.get("mi") or [""])[0]
                    gyeonggi_resolved += 1'''

new = '''            # Canonical URL wins. Generic titles such as "기간제교원 채용 공고" are not unique.
            current_exact = False
            if job.get("url") and "selectNttInfo.do" in job.get("url", ""):
                q = parse_qs(urlparse(job["url"]).query)
                sn = (q.get("nttSn") or [""])[0]
                bbs = (q.get("bbsId") or [""])[0]
                if sn.isdigit() and bbs:
                    job["detailLinkResolved"] = True
                    job["nttSn"] = sn
                    job["bbsId"] = bbs
                    job["mi"] = (q.get("mi") or [""])[0]
                    gyeonggi_resolved += 1
                    current_exact = True
            if not current_exact:
                source_names = job.get("checkedSources") or [job.get("source", "")]
                hit = None
                for office in source_names:
                    hit = all_map.get((office, norm(job.get("title", ""))))
                    if hit:
                        break
                if not hit:
                    hit = all_map.get((job.get("source", ""), norm(job.get("title", ""))))
                if hit:
                    job.update({
                        "url": hit["url"], "boardUrl": hit["boardUrl"],
                        "detailLinkResolved": True, "nttSn": hit["nttSn"],
                        "bbsId": hit["bbsId"], "mi": hit["mi"],
                        "detailResolutionMethod": hit["method"],
                    })
                    gyeonggi_resolved += 1'''

if old in s:
    s = s.replace(old, new, 1)
elif "current_exact = False" not in s:
    raise SystemExit("Unexpected resolver source: canonical-link protection marker missing")

# Deliberately do NOT expand the fallback resolver to 500 pages. complete_support_coverage.py
# already performs exhaustive 90-day traversal and emits canonical URLs. Repeating that scan here
# caused hourly runs to exceed their publication window.
if "for page in range(1, 9):" not in s:
    # A previously runtime-patched copy may still contain the expensive loop; shrink only that loop.
    s = s.replace("for page in range(1, 501):", "for page in range(1, 9):", 1)

if "current_exact = False" not in s or "for page in range(1, 9):" not in s:
    raise SystemExit("Fast exact-link resolver safety markers missing")

p.write_text(s, encoding="utf-8")
print("Exact-link resolver protected; exhaustive traversal remains in completeness crawler only")
