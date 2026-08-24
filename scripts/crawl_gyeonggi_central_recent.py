#!/usr/bin/env python3
"""Crawl the Gyeonggi central recruitment portal for the full recent 90-day window.

The normal list UI exposes a `마감제외` checkbox. The fast collector intentionally follows that
active-oriented view, but completeness reconciliation must not: recently closed postings are still
part of the 90-day audit population. This crawler disables `srchEcptDl`, follows registration-date
ordering, and requires two consecutive fully-old pages (or a structural end) before declaring the
90-day boundary crossed. It returns the same job shape used by scrape_jobs.py and fails closed when
coverage cannot be proven.

Important invariant: every posting admitted to the 90-day completeness population must have a
parseable registration date. The official portal retains up to one year of material, so treating an
unparseable date as "recent" can silently inflate a 90-day audit into a one-year crawl.
"""
import re
import time
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

import scrape_jobs as primary

LOOKBACK_DAYS = 90


def _date(value):
    return primary.date_norm(value or "")


def _old(value):
    if not value:
        return False
    try:
        d = datetime.strptime(value, "%Y/%m/%d").replace(tzinfo=primary.KST)
        return d < primary.NOW - timedelta(days=LOOKBACK_DAYS)
    except Exception:
        return False


def scrape_gyeonggi_central_recent():
    central_url = primary.GYEONGGI["central"]["url"]
    jobs = []
    seen_ids = set()
    coverage = []

    for code, (cat_name, ui_type) in primary.GYEONGGI_CATEGORIES.items():
        pages = 0
        raw_rows = 0
        accepted_rows = 0
        crossed = False
        natural_end = False
        access_error = False
        repeated = False
        consecutive_old_pages = 0
        previous_page_ids = None

        for page in range(1, primary.CENTRAL_MAX_PAGES + 1):
            data = {
                "mi": "10502", "pbancSn": "", "currPage": str(page),
                # IMPORTANT: blank means include closed postings; Y is the official UI's '마감제외'.
                "srchEcptDl": "", "srchTodayPb": "", "srchLgnNm": "",
                "srchOcptNm": cat_name, "srchOcptCd": code, "pageIndex": "50",
                "orderbyType": "reg", "searchType": "", "searchValue": "",
                "btchDlYn": "", "cndNo": "", "srchSchlSe": "",
            }
            r = primary.post(central_url, data)
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

            page_ids = []
            page_dates = []
            parsed_rows = []
            undated = []
            for li in rows:
                a = li.find("a", href=re.compile(r"goView\(['\"]?\d+"))
                if not a:
                    continue
                m = re.search(r"goView\(['\"]?(\d+)", a.get("href", ""))
                if not m:
                    continue
                pid = m.group(1)
                top = [primary.clean(x.get_text(" ", strip=True)) for x in li.select(".cont_top span")]
                registered = next((_date(x) for x in top if "등록일" in x and _date(x)), "")
                page_ids.append(pid)
                if registered:
                    page_dates.append(registered)
                else:
                    undated.append({
                        "id": pid,
                        "text": primary.clean(li.get_text(" ", strip=True))[:180],
                    })
                parsed_rows.append((pid, li, top, registered))

            # Fail closed. A missing registration date destroys the proof that this is a 90-day
            # population; continuing could pull the portal's full one-year retention window.
            if undated:
                raise RuntimeError(
                    f"Gyeonggi central registration-date parse failure on {cat_name} page {page}: "
                    f"{undated[:3]}"
                )
            if page_ids and len(page_dates) != len(page_ids):
                raise RuntimeError(
                    f"Gyeonggi central date/id mismatch on {cat_name} page {page}: "
                    f"ids={len(page_ids)} dates={len(page_dates)}"
                )

            sig = tuple(page_ids)
            if sig and sig == previous_page_ids:
                repeated = True
                break
            previous_page_ids = sig

            for pid, li, top, registered in parsed_rows:
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                if _old(registered):
                    continue
                school = top[0] if top else ""
                title_el = li.select_one(".cont_tit")
                title = primary.clean(title_el.get_text(" ", strip=True) if title_el else "")
                title = primary.clean(re.sub(r"^(마감임박|오늘등록|NEW)\s*", "", title))
                vals = primary.parse_gyeonggi_labelled(li)
                apply_start, apply_end = primary.split_period(vals.get("접수기간", ""))
                work_start, work_end = primary.split_period(vals.get("채용기간", ""))
                body_text = primary.clean(li.get_text(" ", strip=True))
                region = primary.find_region(body_text, primary.GYEONGGI_REGIONS)
                jobs.append({
                    "id": "goe-" + pid, "province": "경기", "school": school, "title": title,
                    "subject": vals.get("직무분야", ""), "region": region,
                    "regions": [region] if region else [], "type": ui_type,
                    "schoolLevel": primary.normalize_school_level("", school, title),
                    "applyStart": apply_start, "applyEnd": apply_end,
                    "workStart": work_start, "workEnd": work_end, "registered": registered,
                    "headcount": re.sub(r"[^0-9명]+", "", vals.get("채용인원", "")),
                    "source": primary.GYEONGGI["central"]["name"],
                    "checkedSources": [primary.GYEONGGI["central"]["name"]],
                    "sourceType": "통합게시판",
                    "url": f"https://www.goe.go.kr/recruit/ad/func/pb/hnfpPbancInfoView.do?mi=10502&pbancSn={pid}",
                })
                accepted_rows += 1

            if page_dates and all(_old(x) for x in page_dates):
                consecutive_old_pages += 1
            else:
                consecutive_old_pages = 0
            if consecutive_old_pages >= 2:
                crossed = True
                break
            if len(rows) < 45:
                natural_end = True
                break
            time.sleep(0.05)

        ok = (crossed or natural_end) and not access_error and not repeated and pages < primary.CENTRAL_MAX_PAGES
        coverage.append({
            "code": code, "name": cat_name, "pagesScanned": pages,
            "rawRows": raw_rows, "acceptedRecentRows": accepted_rows,
            "crossedLookback": crossed, "naturalEnd": natural_end,
            "accessError": access_error, "paginationRepeated": repeated,
            "coverageComplete": ok,
        })
        if not ok:
            raise RuntimeError(f"Gyeonggi central 90-day coverage incomplete: {coverage[-1]}")

    scrape_gyeonggi_central_recent.last_coverage = coverage
    return jobs


scrape_gyeonggi_central_recent.last_coverage = []


if __name__ == "__main__":
    rows = scrape_gyeonggi_central_recent()
    print({"jobs": len(rows), "coverage": scrape_gyeonggi_central_recent.last_coverage})
