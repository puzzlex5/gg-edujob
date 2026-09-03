#!/usr/bin/env python3
"""Read-only onboarding probe for JobTeacher.

This script does NOT publish JobTeacher jobs.  It discovers the public list
structure, candidate durable identifiers and pagination evidence so a dedicated
collector can be implemented without guessing or mixing unverified data into the
unified search.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
BASE = "https://www.jobteacher.kr"
TARGETS = {
    "all": f"{BASE}/employ/lists",
    "seoul": f"{BASE}/employ/lists/all/?is_search=yes&sel_area%5B%5D=1",
    "gyeonggi": f"{BASE}/employ/lists/all/?is_search=yes&sel_area%5B%5D=2422",
}
ID_KEYS = {"idx", "no", "seq", "id", "employ_no", "employ_idx", "recruit_no", "recruit_idx"}
PAGE_KEYS = {"page", "pageno", "page_no", "nowpage", "p", "start"}
BLOCK_RE = re.compile(r"captcha|자동입력|사람인지|접근이\s*제한|비정상적인\s*접근", re.I)
METRO_RE = re.compile(r"서울|경기")
JOB_RE = re.compile(r"채용|구인|강사|선생님|교사|모집")


def fetch(session: requests.Session, url: str) -> tuple[str, int, str]:
    r = session.get(url, timeout=30, allow_redirects=True)
    return r.text, r.status_code, r.url


def fingerprint(items: list[str]) -> str:
    payload = "\n".join(items).encode("utf-8", "ignore")
    return hashlib.sha256(payload).hexdigest()[:16]


def normalized_url(base: str, href: str) -> str:
    return urljoin(base, href.strip())


def candidate_id(url: str) -> tuple[str, str] | None:
    q = parse_qs(urlparse(url).query)
    for key, vals in q.items():
        if key.lower() in ID_KEYS and vals and str(vals[0]).strip():
            return key, str(vals[0]).strip()
    m = re.search(r"/(?:view|detail|read|info)/(\d+)(?:/|$|\?)", url, re.I)
    if m:
        return "path-id", m.group(1)
    return None


def is_jobish_url(url: str) -> bool:
    p = urlparse(url)
    s = (p.path + "?" + p.query).lower()
    if "/employ/" not in s:
        return False
    if "/lists" in p.path.lower() and not candidate_id(url):
        return False
    return bool(candidate_id(url) or re.search(r"view|detail|read|info", s))


def extract_urls(soup: BeautifulSoup, base: str) -> list[str]:
    out: list[str] = []
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if href and not href.lower().startswith(("javascript:", "#", "mailto:", "tel:")):
            out.append(normalized_url(base, href))
        onclick = a.get("onclick") or ""
        for raw in re.findall(r"['\"]([^'\"]*(?:/employ/|employ/)[^'\"]*)['\"]", onclick, re.I):
            out.append(normalized_url(base, raw))
    return list(dict.fromkeys(out))


def row_samples(soup: BeautifulSoup) -> list[str]:
    samples: list[str] = []
    for tr in soup.find_all("tr"):
        text = " ".join(tr.stripped_strings)
        if len(text) >= 12 and METRO_RE.search(text) and JOB_RE.search(text):
            samples.append(re.sub(r"\s+", " ", text)[:500])
    if samples:
        return samples[:30]
    # Some responsive pages do not use table rows.
    for tag in soup.find_all(["li", "div"]):
        text = " ".join(tag.stripped_strings)
        if 20 <= len(text) <= 800 and METRO_RE.search(text) and JOB_RE.search(text):
            samples.append(re.sub(r"\s+", " ", text)[:500])
            if len(samples) >= 30:
                break
    return samples


def pagination_urls(urls: list[str]) -> list[str]:
    out = []
    for u in urls:
        q = parse_qs(urlparse(u).query)
        if any(k.lower() in PAGE_KEYS for k in q):
            out.append(u)
    return list(dict.fromkeys(out))


def force_page_two(url: str) -> str:
    p = urlparse(url)
    q = parse_qs(p.query, keep_blank_values=True)
    q["page"] = ["2"]
    query = urlencode([(k, v) for k, vals in q.items() for v in vals])
    return urlunparse((p.scheme, p.netloc, p.path, p.params, query, p.fragment))


def probe_surface(session: requests.Session, name: str, url: str) -> dict:
    html, status, final_url = fetch(session, url)
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.stripped_strings)
    urls = extract_urls(soup, final_url)
    job_urls = [u for u in urls if is_jobish_url(u)]
    ids = []
    for u in job_urls:
        cid = candidate_id(u)
        if cid:
            ids.append({"key": cid[0], "value": cid[1], "url": u})
    ids = list({(x["key"], x["value"], x["url"]): x for x in ids}.values())
    pages = pagination_urls(urls)
    rows = row_samples(soup)

    page2_url = next((u for u in pages if re.search(r"(?:page|pageno|page_no|nowpage|\bp)=?[^&]*2", u, re.I)), None)
    if not page2_url:
        page2_url = force_page_two(final_url)
    p2_html, p2_status, p2_final = fetch(session, page2_url)
    p2_soup = BeautifulSoup(p2_html, "html.parser")
    p2_rows = row_samples(p2_soup)

    blocked = bool(BLOCK_RE.search(text))
    reachable = status == 200 and len(html) > 5000 and not blocked
    pagination_changed = p2_status == 200 and fingerprint(rows) != fingerprint(p2_rows) and bool(p2_rows)
    return {
        "surface": name,
        "requestedUrl": url,
        "finalUrl": final_url,
        "httpStatus": status,
        "htmlBytes": len(html.encode("utf-8", "ignore")),
        "blockedOrHumanCheck": blocked,
        "reachable": reachable,
        "metroTextPresent": bool(METRO_RE.search(text)),
        "jobLikeUrlCount": len(job_urls),
        "candidateStableIdCount": len(ids),
        "candidateStableIdExamples": ids[:20],
        "paginationLinkCount": len(pages),
        "paginationExamples": pages[:10],
        "rowSampleCount": len(rows),
        "rowSamples": rows[:10],
        "page2Url": p2_final,
        "page2HttpStatus": p2_status,
        "page2RowSampleCount": len(p2_rows),
        "paginationChangesContent": pagination_changed,
    }


def main() -> int:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; MetroEduJobSourceProbe/1.0; +https://puzzlex5.github.io/gg-edujob/)",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
    })

    surfaces = []
    errors = []
    for name, url in TARGETS.items():
        try:
            surfaces.append(probe_surface(session, name, url))
        except Exception as exc:  # diagnostic probe must preserve evidence from other surfaces
            errors.append({"surface": name, "error": f"{type(exc).__name__}: {exc}"})

    metro = [s for s in surfaces if s.get("surface") in {"seoul", "gyeonggi"}]
    reachable = len(metro) == 2 and all(s.get("reachable") for s in metro)
    stable_id_evidence = all(int(s.get("candidateStableIdCount") or 0) > 0 for s in metro) if metro else False
    pagination_evidence = all(s.get("paginationChangesContent") is True for s in metro) if metro else False
    ready = reachable and stable_id_evidence and pagination_evidence and not errors

    report = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "잡티처",
        "baseUrl": BASE,
        "mode": "read-only-onboarding-probe",
        "publicationEnabled": False,
        "surfaces": surfaces,
        "summary": {
            "metroSurfacesReachable": reachable,
            "stableIdEvidence": stable_id_evidence,
            "paginationEvidence": pagination_evidence,
            "readyForDedicatedCollector": ready,
            "errors": len(errors),
        },
        "errors": errors,
        "policy": {
            "publishBeforeValidation": False,
            "regionsAllowed": ["서울", "경기"],
            "nextGate": "durable ID + complete pagination + current-job classification + missingAfter=0",
        },
    }
    Path("jobteacher_probe_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if surfaces else 2


if __name__ == "__main__":
    raise SystemExit(main())
