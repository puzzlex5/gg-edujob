#!/usr/bin/env python3
"""Discover newly linked official recruitment boards without blindly trusting every page.

Strong Gyeonggi MirCMS recruitment links are safe to add to board_cache.json automatically.
Ambiguous candidates (especially Seoul custom recruitment pages) are recorded for audit only.
"""
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse, urlencode

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "sources.json"
CACHE_PATH = ROOT / "board_cache.json"
REPORT_PATH = ROOT / "board_discovery_report.json"
SOURCES = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
CACHE = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}

KEYWORDS = re.compile(r"채용|구인|기간제|계약제|강사|공무직|근로자|인력채용|학교인력|채용지원", re.I)
EXCLUDE = re.compile(r"구직|합격자|합격결과|인사발령|자료실|공지사항", re.I)

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (compatible; metro-edujob-board-discovery/1.0)", "Accept-Language": "ko-KR,ko;q=0.9"})
retry = Retry(total=3, connect=3, read=3, status=3, backoff_factor=0.8,
              status_forcelist=(408,429,500,502,503,504), allowed_methods=frozenset(("GET",)),
              respect_retry_after_header=True, raise_on_status=False)
S.mount("https://", HTTPAdapter(max_retries=retry)); S.mount("http://", HTTPAdapter(max_retries=retry))


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def canonical(u):
    try:
        p = urlparse(u)
        q = parse_qs(p.query, keep_blank_values=True)
        keep = {}
        for k in ("bbsId","mi","srch_aditCol1","clasHmpgId"):
            if q.get(k): keep[k] = q[k][0]
        return urlunparse((p.scheme, p.netloc, p.path, "", urlencode(keep), ""))
    except Exception:
        return u


def fetch(url):
    try:
        r = S.get(url, timeout=18, allow_redirects=True)
        if r.status_code >= 400: return None
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception:
        return None


def candidate_pages(home_url, html):
    soup = BeautifulSoup(html, "html.parser")
    urls = [home_url]
    for a in soup.find_all("a", href=True):
        text = clean(a.get_text(" ", strip=True))
        href = urljoin(home_url, a.get("href", ""))
        if KEYWORDS.search(text) and not EXCLUDE.search(text) and urlparse(href).hostname == urlparse(home_url).hostname:
            urls.append(href)
    # Limit secondary exploration: enough to find menu landing pages without turning discovery into another crawler.
    return list(dict.fromkeys(urls))[:18]


def scan_office(src, province):
    office = src.get("name", "")
    home = fetch(src.get("url", ""))
    if not home:
        return {"name": office, "error": "homepage-unavailable", "strong": [], "candidates": []}
    pages = candidate_pages(home.url, home.text)
    strong, candidates = [], []
    seen = set()
    for page in pages:
        r = home if page == home.url else fetch(page)
        if not r: continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            text = clean(a.get_text(" ", strip=True))
            if not KEYWORDS.search(text) or EXCLUDE.search(text):
                continue
            u = urljoin(r.url, a.get("href", ""))
            if urlparse(u).hostname != urlparse(home.url).hostname:
                continue
            cu = canonical(u)
            if cu in seen: continue
            seen.add(cu)
            q = parse_qs(urlparse(cu).query, keep_blank_values=True)
            if province == "gyeonggi" and urlparse(cu).path.endswith("/selectNttList.do") and (q.get("bbsId") or [""])[0].isdigit():
                strong.append({"text": text, "url": cu})
            else:
                candidates.append({"text": text, "url": cu})
    return {"name": office, "error": "", "strong": strong, "candidates": candidates}


def main():
    report = {"gyeonggi": [], "seoul": [], "autoAdded": []}
    changed = False
    for province in ("gyeonggi", "seoul"):
        for src in SOURCES.get(province, {}).get("supportOffices", []):
            result = scan_office(src, province)
            report[province].append(result)
            if province != "gyeonggi":
                continue
            office = src.get("name", "")
            known = list(CACHE.get(office, []))
            known_keys = {canonical(x) for x in known}
            for item in result.get("strong", []):
                u = item["url"]
                if u not in known_keys:
                    known.append(u); known_keys.add(u); changed = True
                    report["autoAdded"].append({"office": office, "text": item["text"], "url": u})
            CACHE[office] = known
    if changed:
        CACHE_PATH.write_text(json.dumps(CACHE, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Board discovery complete: auto-added {len(report['autoAdded'])} strong Gyeonggi board(s)")


if __name__ == "__main__":
    main()
