#!/usr/bin/env python3
"""Read-only onboarding probe for JobTeacher.

This script never publishes JobTeacher jobs. It discovers the public list
structure, candidate durable identifiers and pagination evidence. Verified
requests are preferred; if Python cannot build the site's TLS chain, the probe
falls back to normal Chromium TLS validation rather than disabling certificate
verification.
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
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"


class VerifiedFetcher:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5"})
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self.tls_fallbacks: list[dict[str, str]] = []

    def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(user_agent=UA, locale="ko-KR")
        self._page = self._context.new_page()

    def _chromium_get(self, url: str) -> tuple[str, int, str, str]:
        self._ensure_browser()
        response = self._page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self._page.wait_for_timeout(1200)
        return self._page.content(), (response.status if response else 0), self._page.url, "chromium-verified-tls"

    def get(self, url: str, force_browser: bool = False) -> tuple[str, int, str, str]:
        if not force_browser:
            try:
                r = self.session.get(url, timeout=30, allow_redirects=True)
                return r.text, r.status_code, r.url, "requests-verified-tls"
            except requests.exceptions.SSLError as exc:
                self.tls_fallbacks.append({"url": url, "reason": f"{type(exc).__name__}: {exc}"})
            except requests.RequestException as exc:
                # A browser retry is safe for this read-only probe and can distinguish
                # a client-stack problem from a real source outage.
                self.tls_fallbacks.append({"url": url, "reason": f"request-fallback: {type(exc).__name__}: {exc}"})
        return self._chromium_get(url)

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()


def fingerprint(items: list[str]) -> str:
    return hashlib.sha256("\n".join(items).encode("utf-8", "ignore")).hexdigest()[:16]


def normalized_url(base: str, href: str) -> str:
    return urljoin(base, href.strip())


def candidate_id(url: str) -> tuple[str, str] | None:
    q = parse_qs(urlparse(url).query)
    for key, vals in q.items():
        if key.lower() in ID_KEYS and vals and str(vals[0]).strip():
            return key, str(vals[0]).strip()
    for pattern in (
        r"/(?:view|detail|read|info)/(\d+)(?:/|$|\?)",
        r"/employ/(?:view|detail|read|info)[^/]*?/(\d+)(?:/|$|\?)",
        r"/employ/[^?]+/(\d+)(?:/|$|\?)",
    ):
        m = re.search(pattern, url, re.I)
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
        if len(text) >= 12 and JOB_RE.search(text):
            samples.append(re.sub(r"\s+", " ", text)[:500])
    if samples:
        return samples[:40]
    for tag in soup.find_all(["li", "div", "article"]):
        text = " ".join(tag.stripped_strings)
        if 20 <= len(text) <= 900 and JOB_RE.search(text):
            samples.append(re.sub(r"\s+", " ", text)[:500])
            if len(samples) >= 40:
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


def parse_document(html: str, final_url: str) -> tuple[BeautifulSoup, str, list[str], list[str], list[dict[str, str]], list[str], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.stripped_strings)
    urls = extract_urls(soup, final_url)
    job_urls = [u for u in urls if is_jobish_url(u)]
    ids: list[dict[str, str]] = []
    for u in job_urls:
        cid = candidate_id(u)
        if cid:
            ids.append({"key": cid[0], "value": cid[1], "url": u})
    ids = list({(x["key"], x["value"], x["url"]): x for x in ids}.values())
    return soup, text, urls, job_urls, ids, pagination_urls(urls), row_samples(soup)


def probe_surface(fetcher: VerifiedFetcher, name: str, url: str) -> dict:
    html, status, final_url, transport = fetcher.get(url)
    soup, text, urls, job_urls, ids, pages, rows = parse_document(html, final_url)

    # If static HTML is reachable but the recruitment list is rendered later,
    # retry the same page in fully rendered Chromium without weakening TLS.
    if status == 200 and (not job_urls or not rows) and transport != "chromium-verified-tls":
        b_html, b_status, b_final, b_transport = fetcher.get(url, force_browser=True)
        b_parsed = parse_document(b_html, b_final)
        if len(b_parsed[3]) > len(job_urls) or len(b_parsed[6]) > len(rows):
            html, status, final_url, transport = b_html, b_status, b_final, b_transport
            soup, text, urls, job_urls, ids, pages, rows = b_parsed

    page2_url = next((u for u in pages if re.search(r"(?:page|pageno|page_no|nowpage|\bp)[^0-9]*2(?:&|$)", u, re.I)), None)
    if not page2_url:
        page2_url = force_page_two(final_url)
    p2_html, p2_status, p2_final, p2_transport = fetcher.get(page2_url, force_browser=(transport == "chromium-verified-tls"))
    p2_soup = BeautifulSoup(p2_html, "html.parser")
    p2_rows = row_samples(p2_soup)

    blocked = bool(BLOCK_RE.search(text))
    reachable = status == 200 and len(html) > 5000 and not blocked
    pagination_changed = p2_status == 200 and bool(rows) and bool(p2_rows) and fingerprint(rows) != fingerprint(p2_rows)
    return {
        "surface": name,
        "requestedUrl": url,
        "finalUrl": final_url,
        "httpStatus": status,
        "transport": transport,
        "tlsVerificationDisabled": False,
        "htmlBytes": len(html.encode("utf-8", "ignore")),
        "blockedOrHumanCheck": blocked,
        "reachable": reachable,
        "metroTextPresent": bool(METRO_RE.search(text)),
        "jobLikeUrlCount": len(job_urls),
        "candidateStableIdCount": len(ids),
        "candidateStableIdExamples": ids[:30],
        "paginationLinkCount": len(pages),
        "paginationExamples": pages[:15],
        "rowSampleCount": len(rows),
        "rowSamples": rows[:12],
        "page2Url": p2_final,
        "page2HttpStatus": p2_status,
        "page2Transport": p2_transport,
        "page2RowSampleCount": len(p2_rows),
        "paginationChangesContent": pagination_changed,
    }


def main() -> int:
    fetcher = VerifiedFetcher()
    surfaces: list[dict] = []
    errors: list[dict[str, str]] = []
    try:
        for name, url in TARGETS.items():
            try:
                surfaces.append(probe_surface(fetcher, name, url))
            except Exception as exc:
                errors.append({"surface": name, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        fetcher.close()

    metro = [s for s in surfaces if s.get("surface") in {"seoul", "gyeonggi"}]
    reachable = len(metro) == 2 and all(s.get("reachable") for s in metro)
    stable_id_evidence = bool(metro) and all(int(s.get("candidateStableIdCount") or 0) > 0 for s in metro)
    pagination_evidence = bool(metro) and all(s.get("paginationChangesContent") is True for s in metro)
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
            "verifiedTlsFallbacks": len(fetcher.tls_fallbacks),
        },
        "transportDiagnostics": {
            "requestsTlsFallbacks": fetcher.tls_fallbacks[:20],
            "certificateVerificationDisabled": False,
        },
        "errors": errors,
        "policy": {
            "publishBeforeValidation": False,
            "regionsAllowed": ["서울", "경기"],
            "nextGate": "durable ID + complete pagination + current-job classification + missingAfter=0",
        },
    }
    Path("jobteacher_probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if surfaces else 2


if __name__ == "__main__":
    raise SystemExit(main())
