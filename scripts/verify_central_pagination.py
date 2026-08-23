#!/usr/bin/env python3
"""Independently prove pagination completion for the two central recruitment portals.

This verifier does not trust the collector's row count. It traverses the official list endpoints
with stable source IDs and requires structural termination evidence: an empty page, a short final
page (where applicable), or a page that produces no new stable IDs after earlier pages. Reaching
the emergency ceiling or a request failure before such evidence is incomplete.
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
UA = "Mozilla/5.0 (compatible; metro-edujob-central-auditor/1.0)"

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
    except Exception as e:
        return None


def get(url, params):
    try:
        r = S.get(url, params=params, timeout=22, allow_redirects=True)
        r.raise_for_status()
        return r
    except Exception:
        return None


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
        for page in range(1, MAX_PAGES + 1):
            data = {
                "mi": "10502", "pbancSn": "", "currPage": str(page), "srchEcptDl": "Y",
                "srchTodayPb": "", "srchLgnNm": "", "srchOcptNm": cat_name,
                "srchOcptCd": code, "pageIndex": "50", "orderbyType": "reg",
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
                a = li.find("a", href=re.compile(r"goView\\(['\"]?\\d+"))
                if not a:
                    continue
                m = re.search(r"goView\\(['\"]?(\\d+)", a.get("href", ""))
                if m:
                    ids.append(m.group(1))
            new_ids = [x for x in ids if x not in seen]
            seen.update(ids)
            all_ids.update(ids)
            if not new_ids:
                terminal = "no-new-stable-ids"
                break
            # The portal requests 50 rows/page. A shorter non-empty page is structural final-page evidence.
            if len(rows) < 45:
                terminal = "short-final-page"
                break
        cap_hit = pages >= MAX_PAGES and not terminal
        ok = bool(terminal) and not access_error and not cap_hit
        complete = complete and ok
        categories.append({
            "code": code, "name": cat_name, "pagesScanned": pages, "rawRows": raw_rows,
            "stableIdCount": len(seen), "terminalEvidence": terminal,
            "accessError": access_error, "capHit": cap_hit, "complete": ok,
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

    for page in range(1, MAX_PAGES + 1):
        r = get(base_url, {
            "type": "term", "q_currPage": page, "q_rowPerPage": "50",
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
            a = li.find("a", href=re.compile(r"BD_selectRecDetail\\.do\\?q_rcrtSn="))
            if not a:
                continue
            m = re.search(r"q_rcrtSn=(\\d+)", a.get("href", ""))
            if m:
                ids.append(m.group(1))
        new_ids = [x for x in ids if x not in seen]
        seen.update(ids)
        if not new_ids:
            terminal = "no-new-stable-ids"
            break
        if len(cards) < 45:
            terminal = "short-final-page"
            break

    cap_hit = pages >= MAX_PAGES and not terminal
    complete = bool(terminal) and not access_error and not cap_hit
    return {
        "name": primary.SEOUL["central"]["name"], "province": "서울",
        "stableIdCount": len(seen), "pagesScanned": pages, "rawRows": raw_rows,
        "terminalEvidence": terminal, "accessError": access_error,
        "capHit": cap_hit, "complete": complete,
    }


def main():
    gg = audit_gyeonggi()
    se = audit_seoul()
    report = {
        "generatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "policy": "central sources require structural pagination termination evidence; non-empty first page is never sufficient",
        "sources": [gg, se],
        "complete": bool(gg.get("complete") and se.get("complete")),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if not report["complete"]:
        raise SystemExit("Central pagination completeness not proven")


if __name__ == "__main__":
    main()
