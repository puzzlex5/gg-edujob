#!/usr/bin/env python3
"""Lessoninfo runner with safe explicit-official-link enrichment.

A Lessoninfo posting can point to a school/education-office original. We capture only explicit
outbound URLs carrying a known official posting identity (pbancSn, q_rcrtSn/rcrtSn, nttSn+bbsId,
or job_seq). These links are strong evidence usable by the unified layer; ordinary title similarity
remains review-only.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urljoin, urlparse

import crawl_lessoninfo_browser as b
import run_lessoninfo_browser_fast  # applies the hardened fast load + classify_clean wrappers

_BASE_CLASSIFY = b.classify_detail


def _explicit_official_links(html: str, base_url: str) -> list[str]:
    soup = b.BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select("a[href]"):
        href = urljoin(base_url, a.get("href") or "")
        try:
            p = urlparse(href)
            if p.scheme not in {"http", "https"} or "lessoninfo.co.kr" in (p.hostname or "").lower():
                continue
            q = parse_qs(p.query)
            strong = False
            if str((q.get("pbancSn") or [""])[0]).isdigit(): strong = True
            if str((q.get("q_rcrtSn") or q.get("rcrtSn") or [""])[0]).isdigit(): strong = True
            if str((q.get("job_seq") or [""])[0]).isdigit(): strong = True
            if str((q.get("nttSn") or [""])[0]).isdigit() and str((q.get("bbsId") or [""])[0]).isdigit(): strong = True
            if strong and href not in out:
                out.append(href)
        except Exception:
            continue
    return out[:10]


def classify_with_official_links(html: str, text: str, item: dict):
    job, reason = _BASE_CLASSIFY(html, text, item)
    if job:
        job["linkedOfficialUrls"] = _explicit_official_links(html, item.get("url") or "")
    return job, reason


b.classify_detail = classify_with_official_links

if __name__ == "__main__":
    raise SystemExit(b.main())
