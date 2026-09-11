#!/usr/bin/env python3
"""Reliability wrapper for official cultural-foundation recruitment collection.

Keeps the existing nsart parser, adds bounded network retries for transient DNS/transport
failures, and supports Seoul Cultural Foundation's current official recruitment microsite.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import crawl_official_foundation_jobs as base

KST = base.KST
OUTPUT = base.OUTPUT
REPORT = base.REPORT
UA = base.UA


RETRY = Retry(
    total=4,
    connect=4,
    read=3,
    status=3,
    backoff_factor=1.0,
    status_forcelist=(408, 429, 500, 502, 503, 504),
    allowed_methods=frozenset(("GET",)),
    respect_retry_after_header=True,
    raise_on_status=False,
)


def make_session() -> requests.Session:
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=RETRY))
    s.mount("http://", HTTPAdapter(max_retries=RETRY))
    return s


def resilient_request(session: requests.Session, url: str) -> requests.Response:
    """Bounded transport retry. DNS/connection failures remain fatal after retries."""
    r = session.get(
        url,
        timeout=25,
        headers={
            "User-Agent": UA,
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        },
        allow_redirects=True,
    )
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r


def sfac_rows(session: requests.Session, foundation: dict, url: str) -> tuple[list[dict], dict]:
    """Read the current official Seoul Cultural Foundation recruitment microsite.

    The official SFAC site links applicants to this microsite. It is a current-posting surface,
    not an archive: a posting is emitted only while its application/announcement period is still
    open. Closed historical notices therefore do not become fake current jobs.
    """
    r = resilient_request(session, url)
    soup = BeautifulSoup(r.text, "html.parser")
    text = base.normalize_space(soup.get_text(" ", strip=True))
    title = ""
    for selector in ("h1", "h2", "h3", ".title", ".recruit-title"):
        for node in soup.select(selector):
            candidate = base.normalize_space(node.get_text(" ", strip=True))
            if candidate and ("채용" in candidate or "모집" in candidate or "공고" in candidate):
                title = candidate
                break
        if title:
            break
    if not title:
        m = re.search(r"(서울문화재단[^\n]{0,120}(?:채용|모집|공고)[^\n]{0,120})", text)
        title = base.normalize_space(m.group(1)) if m else ""

    registered = base.parse_date_text(text[:2500])
    if not registered:
        registered = base.detail_registered(soup, None)
    today = datetime.now(KST).date()
    apply_end = base.extract_apply_end(text, registered)
    active = bool(title and registered and registered <= today and (not apply_end or apply_end >= today))
    if base.RESULT_RE.search(title):
        active = False

    rows: list[dict] = []
    if active:
        identity_seed = f"{url}|{title}|{registered.isoformat()}"
        stable = hashlib.sha1(identity_seed.encode("utf-8")).hexdigest()[:18]
        fid = str(foundation.get("id") or "")
        rows.append({
            "sourceIdentity": f"official-foundation:sfac:{stable}",
            "foundationRegistryId": fid,
            "foundationName": foundation.get("name") or "서울문화재단",
            "organization": foundation.get("name") or "서울문화재단",
            "source": foundation.get("name") or "서울문화재단",
            "sourceType": "문화재단 공식채용",
            "sourceSurface": "cultural-foundation",
            "sourceSurfaceLabel": "서울문화재단 공식 채용공고",
            "sourceRole": "primary-official",
            "trustLevel": "공식",
            "province": foundation.get("region") or "서울",
            "region": foundation.get("municipality") or "서울특별시",
            "regions": [foundation.get("municipality") or "서울특별시"],
            "location": " ".join(x for x in [foundation.get("region"), foundation.get("municipality")] if x),
            "title": title,
            "registered": base.format_date(registered),
            "applyEnd": base.format_date(apply_end),
            "url": r.url,
            "originalUrl": r.url,
            "detailUrl": r.url,
            "boardUrl": url,
            "detailLinkVerified": True,
            "detailLinkReason": "official-sfac-current-microsite",
            "transportVerified": True,
        })

    return rows, {
        "adapter": "sfac-current-microsite",
        "surfacesChecked": [r.url],
        "discoveredDetailLinks": 1 if title else 0,
        "inspectedDetailLinks": 1 if title else 0,
        "publishedCurrentJobs": len(rows),
        "active": active,
        "registered": base.format_date(registered),
        "applyEnd": base.format_date(apply_end),
    }


def main() -> int:
    generated = datetime.now(KST).isoformat(timespec="seconds")
    foundations = base.effective_foundations()
    configured = [x for x in foundations if str(x.get("officialRecruitmentUrl") or "").strip()]
    session = make_session()
    # Reuse the existing, tested nsart adapter but give it the same bounded transport retry policy.
    base.request = resilient_request

    jobs: list[dict] = []
    errors = []
    unsupported = []
    board_results = []

    for foundation in configured:
        board_url = str(foundation.get("officialRecruitmentUrl") or "").strip()
        host = (urlparse(board_url).hostname or "").lower()
        try:
            if host == "nsart.or.kr" or host.endswith(".nsart.or.kr"):
                found, meta = base.nsart_rows(session, foundation, board_url)
            elif host == "sfac.saramin.co.kr":
                found, meta = sfac_rows(session, foundation, board_url)
            else:
                unsupported.append({
                    "foundationRegistryId": foundation.get("id"),
                    "foundationName": foundation.get("name"),
                    "boardUrl": board_url,
                    "reason": "adapter-not-yet-implemented",
                })
                continue
            jobs.extend(found)
            board_results.append({
                "foundationRegistryId": foundation.get("id"),
                "foundationName": foundation.get("name"),
                "boardUrl": board_url,
                "healthy": True,
                **meta,
            })
        except Exception as exc:
            errors.append({
                "foundationRegistryId": foundation.get("id"),
                "foundationName": foundation.get("name"),
                "boardUrl": board_url,
                "error": f"{type(exc).__name__}: {exc}",
            })
            board_results.append({
                "foundationRegistryId": foundation.get("id"),
                "foundationName": foundation.get("name"),
                "boardUrl": board_url,
                "healthy": False,
            })

    ids = [str(x.get("sourceIdentity") or "") for x in jobs]
    urls = [str(x.get("url") or "") for x in jobs]
    duplicate_ids = sorted({x for x in ids if x and ids.count(x) > 1})
    duplicate_urls = sorted({x for x in urls if x and urls.count(x) > 1})
    if duplicate_ids or duplicate_urls:
        errors.append({"error": "duplicate-official-identities", "ids": duplicate_ids, "urls": duplicate_urls})

    jobs.sort(key=lambda x: (str(x.get("registered") or ""), str(x.get("sourceIdentity") or "")), reverse=True)
    healthy = not errors and not unsupported
    payload = {"generatedAt": generated, "sourceRole": "primary-official", "jobs": jobs}
    report = {
        "generatedAt": generated,
        "policy": "official-foundation-primary-fail-closed-v3-retry-and-sfac",
        "healthy": healthy,
        "registryInstitutions": len(foundations),
        "officialBoardsConfigured": len(configured),
        "supportedBoardsChecked": len(board_results),
        "unsupportedConfiguredBoards": unsupported,
        "jobs": len(jobs),
        "errors": errors,
        "boards": board_results,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
