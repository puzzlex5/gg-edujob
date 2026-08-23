#!/usr/bin/env python3
"""Independently prove pagination completion for the two central recruitment portals.

This verifier does not trust the collector's row count. It traverses the official list endpoints
with stable source IDs and requires fail-closed structural termination evidence. A non-empty page
whose stable IDs cannot be parsed is a parser failure, not completion. A repeated full page is a
pagination failure, not completion. A short page is treated as final only after the next request
returns empty or repeats that same short page.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import scrape_jobs as primary

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "central_pagination_report.json"
KST = timezone(timedelta(hours=9))
MAX_PAGES = 500
EXPECTED_PAGE_SIZE = 50
UA = "Mozilla/5.0 (compatible; metro-edujob-central-auditor/1.1)"

S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
retry = Retry(
    total=3, connect=3, read=3, status=3, backoff_factor=0.8,
    status_forcelist=(408, 429, 500, 502, 503, 504),
    allowed_methods=frozenset(("GET", "POST")),
    respect_retry_after_header=True, raise_on_status=False,
)
S.mount("https://", HTTPAdapter(max_retries=retry))
S.mount("http://", HTTPAdapter(max_retries=retry))


def post(url, data):
    try:
        r = S.post(url, data=data, timeout=22, allow_redirects=True)
        r.raise_for_status()
        return r
    except Exception:
        return None


def get(url, params):
    try:
        r = S.get(url, params=params, timeout=22, allow_redirects=True)
        r.raise_for_status()
        return r
    except Exception:
        return None


def _finish_state(pages, terminal, access_error, parse_error, pagination_repeated):
    cap_hit = pages >= MAX_PAGES and not terminal
    complete = bool(terminal) and not access_error and not parse_error and not pagination_repeated and not cap_hit
    return cap_hit, complete


def audit_gyeonggi():
    central_url = primary.GYEONGGI["central"]["url"]
    categories = []
    all_ids = set()
    complete = True

    for code, (cat_name, _ui_type) in primary.GYEONGGI_CATEGORIES.items():
        seen = set()
        pages = 0
        raw_rows = 0
        terminal = ""
        access_error = False
        parse_error = False
        pagination_repeated = False
        previous_ids = None
        previous_short = False

        for page in range(1, MAX_PAGES + 1):
            data = {
                "mi": "10502", "pbancSn": "", "currPage": str(page), "srchEcptDl": "Y",
                "srchTodayPb": "", "srchLgnNm": "", "srchOcptNm": cat_name,
                "srchOcptCd": code, "pageIndex": str(EXPECTED_PAGE_SIZE), "orderbyType": "reg",
                "searchType": "", "searchValue": "", "btchDlYn": "", "cndNo": "",
                "srchSchlSe": "",
            }
            r = post(central_url, data)
            if not r:
                access_error = True
                break
            soup = BeautifulSoup(r.text, "html.parser")
            rows = soup.select("div.recruit_list > ul > li")
            pages += 1
            raw_rows += len(rows)
            if not rows:
                terminal = "empty-page"
                break

            ids = []
            for li in rows:
                a = li.find("a", href=re.compile(r"goView\(['\"]?\d+"))
                if not a:
                    continue
                m = re.search(r"goView\(['\"]?(\d+)", a.get("href", ""))
                if m:
                    ids.append(m.group(1))

            # Non-empty official rows with zero stable IDs means the parser no longer matches.
            if not ids:
                parse_error = True
                break

            page_ids = tuple(ids)
            if previous_ids is not None and page_ids == previous_ids:
                if previous_short:
                    terminal = "short-final-page-confirmed-by-repeat"
                else:
                    pagination_repeated = True
                break

            new_ids = [x for x in ids if x not in seen]
            if not new_ids:
                # A repeated/overlapping full page cannot prove that there are no later pages.
                pagination_repeated = True
                break

            seen.update(ids)
            all_ids.update(ids)
            previous_ids = page_ids
            previous_short = len(rows) < EXPECTED_PAGE_SIZE

        cap_hit, ok = _finish_state(pages, terminal, access_error, parse_error, pagination_repeated)
        complete = complete and ok
        categories.append({
            "code": code, "name": cat_name, "pagesScanned": pages, "rawRows": raw_rows,
            "stableIdCount": len(seen), "terminalEvidence": terminal,
            "accessError": access_error, "parseError": parse_error,
            "paginationRepeated": pagination_repeated, "capHit": cap_hit, "complete": ok,
        })

    return {
        "name": primary.GYEONGGI["central"]["name"], "province": "경기",
        "stableIdCount": len(all_ids), "complete": complete,
        "categories": categories,
    }


def audit_seoul():
    base_url = primary.SEOUL["central"]["url"]
    seen = set()
    pages = 0
    raw_rows = 0
    terminal = ""
    access_error = False
    parse_error = False
    pagination_repeated = False
    previous_ids = None
    previous_short = False

    for page in range(1, MAX_PAGES + 1):
        r = get(base_url, {
            "type": "term", "q_currPage": page, "q_rowPerPage": str(EXPECTED_PAGE_SIZE),
            "q_sortBy": "regDt", "q_recClosed": "closed",
        })
        if not r:
            access_error = True
            break
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("#srchDataDiv li.flex_cont")
        pages += 1
        raw_rows += len(cards)
        if not cards:
            terminal = "empty-page"
            break

        ids = []
        for li in cards:
            a = li.find("a", href=re.compile(r"BD_selectRecDetail\.do\?q_rcrtSn="))
            if not a:
                continue
            m = re.search(r"q_rcrtSn=(\d+)", a.get("href", ""))
            if m:
                ids.append(m.group(1))

        if not ids:
            parse_error = True
            break

        page_ids = tuple(ids)
        if previous_ids is not None and page_ids == previous_ids:
            if previous_short:
                terminal = "short-final-page-confirmed-by-repeat"
            else:
                pagination_repeated = True
            break

        new_ids = [x for x in ids if x not in seen]
        if not new_ids:
            pagination_repeated = True
            break

        seen.update(ids)
        previous_ids = page_ids
        previous_short = len(cards) < EXPECTED_PAGE_SIZE

    cap_hit, complete = _finish_state(pages, terminal, access_error, parse_error, pagination_repeated)
    return {
        "name": primary.SEOUL["central"]["name"], "province": "서울",
        "stableIdCount": len(seen), "pagesScanned": pages, "rawRows": raw_rows,
        "terminalEvidence": terminal, "accessError": access_error,
        "parseError": parse_error, "paginationRepeated": pagination_repeated,
        "capHit": cap_hit, "complete": complete,
    }


def main():
    gg = audit_gyeonggi()
    se = audit_seoul()
    report = {
        "generatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "policy": "central sources fail closed: non-empty rows must yield stable IDs; repeated full pages are pagination failures; short pages require one-page confirmation",
        "sources": [gg, se],
        "complete": bool(gg.get("complete") and se.get("complete")),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if not report["complete"]:
        raise SystemExit("Central pagination completeness not proven")


if __name__ == "__main__":
    main()
