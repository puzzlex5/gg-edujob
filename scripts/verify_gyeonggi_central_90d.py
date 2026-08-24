#!/usr/bin/env python3
"""Independently verify the recent 90-day population of the Gyeonggi central recruitment portal.

This intentionally does not call the reconciliation crawler. It uses the official list endpoint,
disables the active-only (마감제외) filter, requires every semantic row to expose both pbancSn and
a parseable registration date, and stops only after two consecutive pages are entirely older than
the 90-day boundary or a structural end is proven. The resulting count is used to cross-check the
38-source reconciliation population.
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
OUT = ROOT / "gyeonggi_central_90d_report.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
LOOKBACK_DAYS = 90
MAX_PAGES = 500
EXPECTED_PAGE_SIZE = 50
UA = "Mozilla/5.0 (compatible; metro-edujob-gyeonggi-90d-auditor/1.0)"

S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
S.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, connect=3, read=3, status=3, backoff_factor=0.8,
    status_forcelist=(408, 429, 500, 502, 503, 504),
    allowed_methods=frozenset(("GET", "POST")), respect_retry_after_header=True,
    raise_on_status=False,
)))


def clean(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()


def parse_registered(li):
    for span in li.select(".cont_top span"):
        text = clean(span.get_text(" ", strip=True))
        if "등록일" not in text:
            continue
        ds = primary.date_norm(text)
        if ds:
            return ds
    return ""


def is_old(ds):
    try:
        d = datetime.strptime(ds, "%Y/%m/%d").replace(tzinfo=KST)
    except Exception:
        return False
    return d < NOW - timedelta(days=LOOKBACK_DAYS)


def extract_id(li):
    for a in li.find_all("a"):
        raw = " ".join((a.get("href") or "", a.get("onclick") or ""))
        for pat in (
            r"goView\s*\(\s*['\"]?(\d+)",
            r"pbancSn\s*[=:,'\"() ]+\s*(\d+)",
            r"(?:hnfpPbanc|PbancView|select\w*Pbanc)[^0-9]{0,80}(\d{4,})",
        ):
            m = re.search(pat, raw, re.I)
            if m:
                return m.group(1)
    return ""


def semantic_row(li):
    html = str(li)
    return bool(re.search(r"(?:goView|pbancSn|hnfpPbanc|PbancView|select\w*Pbanc)", html, re.I))


def post(url, data):
    try:
        r = S.post(url, data=data, timeout=22, allow_redirects=True)
        r.raise_for_status()
        return r
    except Exception:
        return None


def main():
    central_url = primary.GYEONGGI["central"]["url"]
    all_ids = set()
    categories = []
    overall = True

    for code, (cat_name, _ui_type) in primary.GYEONGGI_CATEGORIES.items():
        seen = set()
        pages = 0
        raw_rows = 0
        recent_rows = 0
        consecutive_old = 0
        crossed = False
        natural_end = False
        access_error = False
        parse_error = False
        repeated = False
        previous_ids = None
        error_sample = ""

        for page in range(1, MAX_PAGES + 1):
            data = {
                "mi": "10502", "pbancSn": "", "currPage": str(page),
                "srchEcptDl": "", "srchTodayPb": "", "srchLgnNm": "",
                "srchOcptNm": cat_name, "srchOcptCd": code,
                "pageIndex": str(EXPECTED_PAGE_SIZE), "orderbyType": "reg",
                "searchType": "", "searchValue": "", "btchDlYn": "",
                "cndNo": "", "srchSchlSe": "",
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
                natural_end = True
                break

            parsed = []
            page_ids = []
            page_dates = []
            for li in rows:
                sid = extract_id(li)
                if not sid:
                    if semantic_row(li):
                        parse_error = True
                        error_sample = clean(li.get_text(" ", strip=True))[:180]
                        break
                    continue
                reg = parse_registered(li)
                if not reg:
                    parse_error = True
                    error_sample = clean(li.get_text(" ", strip=True))[:180]
                    break
                parsed.append((sid, reg))
                page_ids.append(sid)
                page_dates.append(reg)
            if parse_error:
                break
            if not page_ids:
                natural_end = True
                break

            sig = tuple(page_ids)
            if previous_ids is not None and sig == previous_ids:
                repeated = True
                break
            previous_ids = sig

            for sid, reg in parsed:
                if sid in seen:
                    continue
                seen.add(sid)
                if not is_old(reg):
                    all_ids.add(sid)
                    recent_rows += 1

            if page_dates and all(is_old(x) for x in page_dates):
                consecutive_old += 1
            else:
                consecutive_old = 0
            if consecutive_old >= 2:
                crossed = True
                break
            if len(page_ids) < EXPECTED_PAGE_SIZE:
                natural_end = True
                break

        cap_hit = pages >= MAX_PAGES and not (crossed or natural_end)
        complete = (crossed or natural_end) and not access_error and not parse_error and not repeated and not cap_hit
        overall = overall and complete
        categories.append({
            "code": code, "name": cat_name, "pagesScanned": pages,
            "rawRows": raw_rows, "recentStableIdCount": recent_rows,
            "crossedLookback": crossed, "naturalEnd": natural_end,
            "accessError": access_error, "parseError": parse_error,
            "parseErrorSample": error_sample, "paginationRepeated": repeated,
            "capHit": cap_hit, "complete": complete,
        })

    report = {
        "generatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "lookbackDays": LOOKBACK_DAYS,
        "populationPolicy": "gyeonggi-central-independent-90d-v1",
        "stableIdCount": len(all_ids),
        "categories": categories,
        "complete": overall,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if not overall:
        raise SystemExit("Independent Gyeonggi central 90-day completeness not proven")


if __name__ == "__main__":
    main()
