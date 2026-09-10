#!/usr/bin/env python3
"""Independently prove pagination completion for the two central recruitment portals.

Structural completeness and publication-population agreement are deliberately separate:
- every semantic recruitment-detail row/card must expose a stable source ID so pagination can be
  proven fail-closed;
- ``stableIdCount`` counts only actual recruitment notices, using the same result/selection-notice
  exclusion policy as the primary collector.

This avoids comparing a collector that intentionally excludes 합격/전형결과 notices with an
independent traversal that previously counted those notices as recruitment IDs. Structural IDs are
still retained in the report so filtering can never hide a parser or pagination failure.
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
UA = "Mozilla/5.0 (compatible; metro-edujob-central-auditor/1.6)"
EMPTY_STATE_RE = re.compile(
    r"검색\s*결과가\s*없|조회(?:된)?\s*(?:자료|데이터|결과)(?:이|가)?\s*없|"
    r"등록된\s*(?:자료|게시물|게시글|공고)(?:이|가)?\s*없|데이터(?:이|가)?\s*없|"
    r"게시물(?:이|가)?\s*없|공고(?:이|가)?\s*없",
    re.I,
)

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


def explicit_empty(soup):
    return bool(EMPTY_STATE_RE.search(soup.get_text(" ", strip=True)))


def extract_gyeonggi_id(li):
    """Extract only a semantically bound recruitment posting ID from one list item."""
    for a in li.find_all("a"):
        raw = " ".join((a.get("href") or "", a.get("onclick") or ""))
        patterns = (
            r"goView\s*\(\s*['\"]?(\d+)",
            r"pbancSn\s*[=:,'\"() ]+\s*(\d+)",
            r"(?:hnfpPbanc|PbancView|select\w*Pbanc)[^0-9]{0,80}(\d{4,})",
        )
        for pat in patterns:
            m = re.search(pat, raw, re.I)
            if m:
                return m.group(1)
    return ""


def gyeonggi_row_has_detail_semantics(li):
    """Return true when a row looks like a recruitment-detail link even if its ID syntax changed."""
    html = str(li)
    if re.search(r"(?:goView|pbancSn|hnfpPbanc|PbancView|select\w*Pbanc)", html, re.I):
        return True
    for a in li.find_all("a"):
        raw = " ".join((a.get("href") or "", a.get("onclick") or ""))
        if re.search(r"(?:detail|view|select).*?(?:recruit|pbanc|hnfp)", raw, re.I):
            return True
    return False


def has_gyeonggi_detail_candidate(rows):
    """Fail closed only for semantic recruitment-detail candidates, not page/menu numbers."""
    return any(extract_gyeonggi_id(li) or gyeonggi_row_has_detail_semantics(li) for li in rows)


def gyeonggi_title(li):
    title_el = li.select_one(".cont_tit")
    title = primary.clean(title_el.get_text(" ", strip=True) if title_el else "")
    return primary.clean(re.sub(r"^(마감임박|오늘등록|NEW)\s*", "", title))


def seoul_title(li):
    title_el = li.select_one(".list_title")
    return primary.clean(title_el.get_text(" ", strip=True) if title_el else "")


def is_recruitment_title(title):
    """Mirror the primary central collectors' intentional result/selection-notice exclusion."""
    return not bool(primary.EXCLUDE_WORDS.search(str(title or "")))


def audit_gyeonggi():
    central_url = primary.GYEONGGI["central"]["url"]
    categories = []
    all_structural_ids = set()
    all_recruitment_ids = set()
    complete = True

    for code, (cat_name, _ui_type) in primary.GYEONGGI_CATEGORIES.items():
        structural_seen = set()
        recruitment_seen = set()
        pages = 0
        raw_rows = 0
        terminal = ""
        access_error = False
        parse_error = False
        pagination_repeated = False
        previous_ids = None
        previous_short = False
        parse_error_sample = ""
        partial_parse_rows = 0

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

            row_pairs = [(li, extract_gyeonggi_id(li)) for li in rows]
            ids = [sid for _li, sid in row_pairs if sid]
            unparsed_detail_rows = [li for li, sid in row_pairs if not sid and gyeonggi_row_has_detail_semantics(li)]

            if ids and unparsed_detail_rows:
                parse_error = True
                partial_parse_rows = len(unparsed_detail_rows)
                parse_error_sample = re.sub(r"\s+", " ", unparsed_detail_rows[0].get_text(" ", strip=True))[:180]
                break

            if not ids:
                if explicit_empty(soup):
                    terminal = "explicit-empty-state"
                elif previous_short and not has_gyeonggi_detail_candidate(rows):
                    terminal = "short-final-page-confirmed-by-nonpost-row"
                else:
                    parse_error = True
                    parse_error_sample = re.sub(r"\s+", " ", rows[0].get_text(" ", strip=True))[:180]
                break

            page_ids = tuple(ids)
            if previous_ids is not None and page_ids == previous_ids:
                if previous_short:
                    terminal = "short-final-page-confirmed-by-repeat"
                else:
                    pagination_repeated = True
                break

            new_ids = [x for x in ids if x not in structural_seen]
            if not new_ids:
                pagination_repeated = True
                break

            structural_seen.update(ids)
            all_structural_ids.update(ids)
            for li, sid in row_pairs:
                if sid and is_recruitment_title(gyeonggi_title(li)):
                    recruitment_seen.add(sid)
                    all_recruitment_ids.add(sid)
            previous_ids = page_ids
            previous_short = len(ids) < EXPECTED_PAGE_SIZE

        cap_hit, ok = _finish_state(pages, terminal, access_error, parse_error, pagination_repeated)
        complete = complete and ok
        categories.append({
            "code": code, "name": cat_name, "pagesScanned": pages, "rawRows": raw_rows,
            "parsedStableIdCount": len(structural_seen),
            "stableIdCount": len(recruitment_seen),
            "excludedNonRecruitmentCount": len(structural_seen - recruitment_seen),
            "terminalEvidence": terminal,
            "accessError": access_error, "parseError": parse_error,
            "partialParseRows": partial_parse_rows, "parseErrorSample": parse_error_sample,
            "paginationRepeated": pagination_repeated, "capHit": cap_hit, "complete": ok,
        })

    return {
        "name": primary.GYEONGGI["central"]["name"], "province": "경기",
        "parsedStableIdCount": len(all_structural_ids),
        "stableIdCount": len(all_recruitment_ids),
        "excludedNonRecruitmentCount": len(all_structural_ids - all_recruitment_ids),
        "complete": complete,
        "categories": categories,
    }


def audit_seoul():
    base_url = primary.SEOUL["central"]["url"]
    structural_seen = set()
    recruitment_seen = set()
    pages = 0
    raw_rows = 0
    terminal = ""
    access_error = False
    parse_error = False
    pagination_repeated = False
    previous_ids = None
    previous_short = False
    partial_parse_cards = 0
    parse_error_sample = ""

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

        card_pairs = []
        unparsed_cards = []
        for li in cards:
            a = li.find("a", href=re.compile(r"BD_selectRecDetail\.do\?q_rcrtSn="))
            if not a:
                unparsed_cards.append(li)
                continue
            m = re.search(r"q_rcrtSn=(\d+)", a.get("href", ""))
            if m:
                card_pairs.append((li, m.group(1)))
            else:
                unparsed_cards.append(li)
        ids = [sid for _li, sid in card_pairs]

        if ids and unparsed_cards:
            parse_error = True
            partial_parse_cards = len(unparsed_cards)
            parse_error_sample = re.sub(r"\s+", " ", unparsed_cards[0].get_text(" ", strip=True))[:180]
            break

        if not ids:
            if explicit_empty(soup):
                terminal = "explicit-empty-state"
            else:
                parse_error = True
                if cards:
                    parse_error_sample = re.sub(r"\s+", " ", cards[0].get_text(" ", strip=True))[:180]
            break

        page_ids = tuple(ids)
        if previous_ids is not None and page_ids == previous_ids:
            if previous_short:
                terminal = "short-final-page-confirmed-by-repeat"
            else:
                pagination_repeated = True
            break

        new_ids = [x for x in ids if x not in structural_seen]
        if not new_ids:
            pagination_repeated = True
            break

        structural_seen.update(ids)
        for li, sid in card_pairs:
            if is_recruitment_title(seoul_title(li)):
                recruitment_seen.add(sid)
        previous_ids = page_ids
        previous_short = len(ids) < EXPECTED_PAGE_SIZE

    cap_hit, complete = _finish_state(pages, terminal, access_error, parse_error, pagination_repeated)
    return {
        "name": primary.SEOUL["central"]["name"], "province": "서울",
        "parsedStableIdCount": len(structural_seen),
        "stableIdCount": len(recruitment_seen),
        "excludedNonRecruitmentCount": len(structural_seen - recruitment_seen),
        "pagesScanned": pages, "rawRows": raw_rows,
        "terminalEvidence": terminal, "accessError": access_error,
        "parseError": parse_error, "partialParseCards": partial_parse_cards,
        "parseErrorSample": parse_error_sample,
        "paginationRepeated": pagination_repeated,
        "capHit": cap_hit, "complete": complete,
    }


def main():
    gg = audit_gyeonggi()
    se = audit_seoul()
    report = {
        "generatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "policy": "central sources fail closed structurally on every semantic detail row/card; parsedStableIdCount proves traversal, while stableIdCount mirrors the primary collector's actual-recruitment population by excluding result/selection notices with the shared EXCLUDE_WORDS policy; repeated full pages remain failures",
        "sources": [gg, se],
        "complete": bool(gg.get("complete") and se.get("complete")),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if not report["complete"]:
        raise SystemExit("Central pagination completeness not proven")


if __name__ == "__main__":
    main()
