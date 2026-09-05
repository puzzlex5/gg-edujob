#!/usr/bin/env python3
"""Deep-discover recruitment details on Seoul support-office CMS list boards.

The standard FUS/JOL11 board is not the only official recruitment surface for every Seoul
support office. This crawler traverses three independently verified CMS list boards until the
90-day lookback is crossed (or the natural end is reached), records exact same-host detail URLs,
and fails closed on access/pagination ambiguity. It does not publish jobs by itself; the existing
conservative CMS recovery step re-fetches and validates each discovered detail before adding it.

These boards are alternative surfaces of existing support offices. They do not increase the
conceptual official-source count beyond the registry's 38 sources.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "seoul_cms_deep_discovery_report.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
LOOKBACK_DAYS = 90
CUTOFF = NOW.date() - timedelta(days=LOOKBACK_DAYS)
MAX_PAGES = 250

BOARDS = [
    {
        "office": "동부교육지원청",
        "url": "https://dbedu.sen.go.kr/CMS/school/school04/school0409/school040903/index.html",
        "label": "학교인력채용지원",
    },
    {
        "office": "북부교육지원청",
        "url": "https://bbedu.sen.go.kr/CMS/notice/notice07/index.html",
        "label": "북부채용정보",
    },
    {
        "office": "성동광진교육지원청",
        "url": "https://sdgjedu.sen.go.kr/CMS/admserv/admserv01/index.html",
        "label": "알림마당(공지사항)",
    },
]

DETAIL_RE = re.compile(r"/CMS/.+/\d+_\d+\.html$", re.I)
DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")
PAGE_TOTAL_RE = re.compile(r"Page\s*\d+\s*/\s*(\d+)", re.I)
PAGE_HREF_RE = re.compile(r"(?:,|%2c)list\d*(?:,|%2c)(\d+)\.html(?:\?.*)?$", re.I)

# Recruitment-like opportunities. Generic committee/event 모집 is intentionally not enough.
RECRUIT_RE = re.compile(
    r"채용|기간제|계약제|교육공무직|공무직|대체직원|대체인력|업무보조원|행정실무사|"
    r"근로자|시간강사|강사|교원|교사|튜터|전문상담|인력풀|전담조사관",
    re.I,
)
# Procedural/status notices are not opportunities even when they contain '채용'.
RESULT_RE = re.compile(
    r"합격자|합격\s*명단|명단\s*공개|응시\s*현황|접수\s*현황|서류\s*(?:심사|전형)\s*(?:합격|결과|대상)?|"
    r"면접\s*(?:대상|일정|결과)|채용\s*(?:결과|완료|취소)|선정\s*결과|최종\s*결과|"
    r"경쟁률|응시\s*인원|접수\s*인원",
    re.I,
)

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; metro-edujob-seoul-cms-deep/1.0; public recruitment aggregator)",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
})
retry = Retry(
    total=3, connect=3, read=3, status=3, backoff_factor=0.8,
    status_forcelist=(408, 429, 500, 502, 503, 504),
    allowed_methods=frozenset(("GET",)), respect_retry_after_header=True, raise_on_status=False,
)
S.mount("https://", HTTPAdapter(max_retries=retry))
S.mount("http://", HTTPAdapter(max_retries=retry))


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_date(text: str):
    m = DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
    except ValueError:
        return None


def get(url: str):
    r = S.get(url, timeout=22, allow_redirects=True)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r


def page_number_from_href(href: str):
    decoded = unquote(href or "")
    m = PAGE_HREF_RE.search(decoded)
    return int(m.group(1)) if m else None


def next_page_url(soup: BeautifulSoup, current_url: str, next_page: int):
    host = (urlparse(current_url).hostname or "").lower()
    for a in soup.find_all("a", href=True):
        href = a.get("href") or ""
        if page_number_from_href(href) != next_page:
            continue
        absolute = urljoin(current_url, href)
        if (urlparse(absolute).hostname or "").lower() == host:
            return absolute
    return ""


def extract_rows(soup: BeautifulSoup, page_url: str):
    host = (urlparse(page_url).hostname or "").lower()
    rows = []
    for tr in soup.find_all("tr"):
        row_text = clean(tr.get_text(" ", strip=True))
        registered = parse_date(row_text)
        candidate = None
        for a in tr.find_all("a", href=True):
            absolute = urljoin(page_url, a.get("href") or "")
            if (urlparse(absolute).hostname or "").lower() != host:
                continue
            if not DETAIL_RE.search(urlparse(absolute).path):
                continue
            title = clean(a.get_text(" ", strip=True))
            if len(title) < 4:
                continue
            candidate = (absolute, title)
            break
        if candidate:
            rows.append({
                "url": candidate[0],
                "text": candidate[1],
                "registered": registered.isoformat() if registered else "",
                "rowText": row_text[:1000],
            })
    return rows


def crawl_board(spec: dict):
    page_url = spec["url"]
    seen_pages = set()
    seen_details = set()
    candidates = []
    pages_scanned = 0
    rows_seen = 0
    old_streak = 0
    total_pages = None
    stop_reason = "max-pages"
    error = ""

    for page_no in range(1, MAX_PAGES + 1):
        if page_url in seen_pages:
            error = f"repeated-page-url:{page_no}"
            stop_reason = "pagination-repeat"
            break
        seen_pages.add(page_url)
        try:
            r = get(page_url)
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
            stop_reason = "access-error"
            break

        soup = BeautifulSoup(r.text, "html.parser")
        body_text = clean(soup.get_text(" ", strip=True))
        if total_pages is None:
            m = PAGE_TOTAL_RE.search(body_text)
            total_pages = int(m.group(1)) if m else None
        rows = extract_rows(soup, r.url)
        pages_scanned += 1
        rows_seen += len(rows)

        dated = []
        for item in rows:
            d = parse_date(item.get("registered", ""))
            if d:
                dated.append(d)
            if item["url"] in seen_details:
                continue
            seen_details.add(item["url"])
            if not d or d < CUTOFF or d > NOW.date() + timedelta(days=1):
                continue
            title = item["text"]
            if not RECRUIT_RE.search(title) or RESULT_RE.search(title):
                continue
            candidates.append(item)

        if dated and all(d < CUTOFF for d in dated):
            old_streak += 1
        else:
            old_streak = 0
        if old_streak >= 2:
            stop_reason = "lookback-crossed"
            break

        if total_pages is not None and page_no >= total_pages:
            stop_reason = "natural-end"
            break

        nxt = next_page_url(soup, r.url, page_no + 1)
        if not nxt:
            if total_pages is None and rows:
                stop_reason = "natural-end-unpaged"
                break
            error = f"missing-next-page-link:{page_no + 1}/{total_pages or '?'}"
            stop_reason = "pagination-incomplete"
            break
        page_url = nxt
        time.sleep(0.04)

    complete = not error and stop_reason in {"lookback-crossed", "natural-end", "natural-end-unpaged"}
    return {
        "office": spec["office"],
        "label": spec["label"],
        "boardUrl": spec["url"],
        "pagesScanned": pages_scanned,
        "totalPages": total_pages,
        "rowsSeen": rows_seen,
        "detailIdsSeen": len(seen_details),
        "acceptedRecentRecruitment": len(candidates),
        "stopReason": stop_reason,
        "coverageComplete": complete,
        "error": error,
        "candidates": candidates,
    }


def main() -> int:
    board_reports = [crawl_board(spec) for spec in BOARDS]
    healthy = bool(board_reports) and all(row["coverageComplete"] for row in board_reports)
    by_office = []
    for row in board_reports:
        by_office.append({
            "name": row["office"],
            "boardUrl": row["boardUrl"],
            "coverageComplete": row["coverageComplete"],
            "candidates": row["candidates"],
        })
    result = {
        "generatedAt": NOW.isoformat(timespec="seconds"),
        "policy": "verified-seoul-cms-list-pagination-90d-v1",
        "lookbackDays": LOOKBACK_DAYS,
        "healthy": healthy,
        "boards": board_reports,
        "seoul": by_office,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "healthy": healthy,
        "boards": [
            {k: row[k] for k in ("office", "pagesScanned", "rowsSeen", "acceptedRecentRecruitment", "stopReason", "coverageComplete", "error")}
            for row in board_reports
        ],
    }, ensure_ascii=False))
    return 0 if healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
