#!/usr/bin/env python3
"""Robust second-pass collector for Gyeonggi education support-office MirCMS boards.

The primary collector historically treated a board as broken when it could not
extract MirCMS detail-link JavaScript even though the board table was visible.
This pass reads the table structure first, treats visible rows as proof that the
board is healthy, and merges recent recruitment rows into jobs.json.
"""
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "sources.json"
JOBS_PATH = ROOT / "jobs.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
UA = "Mozilla/5.0 (compatible; metro-edujob-gyeonggi-support/2.0)"
DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")
JOB_WORDS = re.compile(r"채용|구인|모집|기간제|계약제|시간강사|강사|교사|교원|공무직|사무직|근로자|조리|돌봄|보육|봉사|튜터|안전지킴이|외부강사")
EXCLUDE_WORDS = re.compile(r"최종\s*합격|합격자|서류\s*심사|서류전형|면접\s*대상|선정\s*결과|채용\s*결과|전형\s*결과|합격\s*공고|인사발령")
BOARD_WORDS = re.compile(r"구인|채용정보|채용공고|모집공고|기간제교사|기간제교원|교육공무직")


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def norm(value):
    value = unicodedata.normalize("NFKC", clean(value)).lower()
    return re.sub(r"[^0-9a-z가-힣]+", "", value)


def date_norm(value):
    m = DATE_RE.search(value or "")
    if not m:
        return ""
    return f"{int(m.group(1)):04d}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"


def recent_enough(value, days=150):
    if not value:
        return True
    try:
        dt = datetime.strptime(value, "%Y/%m/%d").replace(tzinfo=KST)
        return dt >= NOW - timedelta(days=days)
    except Exception:
        return True


def guess_type(text):
    text = text or ""
    if any(x in text for x in ("교육공무직", "기간제근로", "대체근로", "조리실무", "조리원", "돌봄전담", "보육전담")):
        return "교육공무직/기간제근로자"
    if any(x in text for x in ("자원봉사", "봉사자", "안전지킴이")):
        return "자원봉사"
    if "시간강사" in text or "강사" in text or "튜터" in text or "시간제" in text:
        return "시간강사/강사"
    if "기간제" in text or "계약제교원" in text or "교원" in text or "교사" in text:
        return "기간제교원"
    return "기타"


def guess_school_level(text):
    text = text or ""
    if "특수학교" in text:
        return "특수학교"
    if "유치원" in text or "병설유" in text:
        return "유치원"
    if "초등학교" in text or re.search(r"[가-힣]+초\b", text):
        return "초등학교"
    if "중학교" in text or re.search(r"[가-힣]+중\b", text):
        return "중학교"
    if "고등학교" in text or re.search(r"[가-힣]+고\b", text):
        return "고등학교"
    if "교육지원청" in text or "교육청" in text:
        return "교육행정기관"
    return "기타"


def school_from_title(title):
    m = re.match(r"\s*[\[(]([^\])]+)[\])]", title or "")
    if m:
        return clean(m.group(1))
    m = re.search(r"([가-힣A-Za-z0-9·]+(?:유치원|초등학교|중학교|고등학교|학교))", title or "")
    return clean(m.group(1)) if m else ""


def first_value(mapping, needles):
    for needle in needles:
        for key, value in mapping.items():
            if needle in key and value:
                return value
    return ""


def construct_detail(board_url, ntt_sn):
    p = urlparse(board_url)
    q = parse_qs(p.query)
    params = {}
    for key in ("bbsId", "mi", "clasHmpgId"):
        if q.get(key):
            params[key] = q[key][0]
    params["nttSn"] = str(ntt_sn)
    path = p.path.replace("selectNttList.do", "selectNttInfo.do")
    return urlunparse((p.scheme, p.netloc, path, "", urlencode(params), ""))


def detail_url(board_url, row):
    # 1) normal href or current MirCMS data-id identifier.
    for a in row.find_all("a"):
        href = a.get("href", "") or ""
        if "selectNttInfo.do" in href:
            return urljoin(board_url, href)
        data_id = clean(str(a.get("data-id", "")))
        if re.fullmatch(r"\d{5,10}", data_id):
            return construct_detail(board_url, data_id)

    # 2) data-* attributes or javascript/onclick. MirCMS deployments vary.
    html = str(row)
    patterns = [
        r"(?:data[-_]?ntt[-_]?sn|nttSn|ntt_sn)\s*[=:]['\" ]*(\d{4,})",
        r"(?:selectNttInfo|fncNttView|fnNttView|nttView|goView)\s*\([^)]*?['\"]?(\d{4,})",
        r"(?:selectNttInfo|fncNttView|fnNttView|nttView|goView)[^0-9]{0,100}(\d{4,})",
    ]
    for pat in patterns:
        m = re.search(pat, html, re.I)
        if m:
            return construct_detail(board_url, m.group(1))

    for tag in row.find_all(True):
        for key, value in tag.attrs.items():
            key_l = str(key).lower().replace("_", "-")
            values = value if isinstance(value, list) else [value]
            if "ntt" in key_l or "sn" in key_l:
                for raw in values:
                    m = re.search(r"\b(\d{4,})\b", str(raw))
                    if m:
                        return construct_detail(board_url, m.group(1))
    return ""


def fetch_board(session, board_url, src):
    regions = src.get("regions", [])
    office = src["name"]
    collected = []
    raw_rows = 0
    pages_ok = 0
    explicit_empty = False
    seen = set()
    seen_page_signatures = set()
    consecutive_old_pages = 0
    stop_reason = ""
    late_error = ""
    page = 1

    while True:
        p = urlparse(board_url)
        q = parse_qs(p.query, keep_blank_values=True)
        q["currPage"] = [str(page)]
        page_url = urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q, doseq=True), p.fragment))
        try:
            r = session.get(page_url, timeout=20, allow_redirects=True)
            r.raise_for_status()
        except Exception as exc:
            if page == 1:
                return collected, {"url": board_url, "rawRows": 0, "pagesOk": 0, "explicitEmpty": False, "error": f"{type(exc).__name__}: {str(exc)[:100]}", "stopReason": "first_page_error", "crossedLookback": False}
            late_error = f"{type(exc).__name__}: {str(exc)[:100]}"
            stop_reason = "later_page_error"
            break

        pages_ok += 1
        soup = BeautifulSoup(r.text, "html.parser")
        page_text = clean(soup.get_text(" ", strip=True))
        if re.search(r"전체\s*0\s*건|총\s*0\s*건|등록된\s*게시물이\s*없", page_text):
            explicit_empty = True

        heading = soup.find("h2") or soup.find("h3") or soup.title
        board_label = clean(heading.get_text(" ", strip=True) if heading else "")
        page_table_rows = 0
        page_recent_rows = 0
        page_dates = []
        page_row_keys = []
        page_dates = []
        page_row_keys = []

        for table in soup.find_all("table"):
            header_row = table.find("tr")
            headers = [clean(x.get_text(" ", strip=True)) for x in header_row.find_all("th")] if header_row else []
            title_idx = next((i for i, h in enumerate(headers) if "제목" in h or "공고명" in h), None)
            date_idx = next((i for i, h in enumerate(headers) if "등록일" in h or "작성일" in h), None)
            if title_idx is None:
                continue

            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if not tds or title_idx >= len(tds):
                    continue
                title_cell = tds[title_idx]
                title = clean(title_cell.get_text(" ", strip=True)).replace("새로운 글", "").strip()
                if len(title) < 3:
                    continue

                page_table_rows += 1
                raw_rows += 1
                vals = {}
                for i, td in enumerate(tds):
                    if i < len(headers) and headers[i]:
                        vals[headers[i]] = clean(td.get_text(" ", strip=True))
                row_text = clean(tr.get_text(" ", strip=True))
                registered = date_norm(vals.get(headers[date_idx], "")) if date_idx is not None and date_idx < len(headers) else ""
                if not registered:
                    dates = [date_norm(m.group(0)) for m in DATE_RE.finditer(row_text)]
                    registered = dates[-1] if dates else ""
                page_row_keys.append((norm(title), registered))
                if registered:
                    page_dates.append(registered)

                if registered and not recent_enough(registered):
                    continue
                if EXCLUDE_WORDS.search(title):
                    continue
                if not JOB_WORDS.search(title) and not BOARD_WORDS.search(board_label):
                    continue

                page_recent_rows += 1
                detail = detail_url(r.url, tr)
                # A list page is not an individual announcement. Keep the job visible,
                # but leave url empty until the exact nttSn detail URL is resolved.
                link = detail
                key = (norm(title), registered, detail or board_url)
                if key in seen:
                    continue
                seen.add(key)

                school = first_value(vals, ["학교명", "기관명"]) or school_from_title(title)
                region = first_value(vals, ["지역"])
                if region not in regions:
                    region = next((x for x in regions if x in (row_text + " " + title + " " + school)), "")
                if not region and len(regions) == 1:
                    region = regions[0]
                raw_type = first_value(vals, ["직종", "구분", "고용형태", "분야"])
                raw_level = first_value(vals, ["학교급별", "학교급", "급별"])
                apply_end = date_norm(first_value(vals, ["접수마감일", "마감일"]))
                digest = hashlib.sha1(f"{office}|{title}|{registered}|{link}".encode("utf-8")).hexdigest()[:18]
                collected.append({
                    "id": "goe-office2-" + digest,
                    "province": "경기",
                    "school": school or office,
                    "title": title,
                    "subject": first_value(vals, ["과목", "분야"]),
                    "region": region,
                    "regions": regions,
                    "type": guess_type(raw_type + " " + title),
                    "schoolLevel": guess_school_level((raw_level or "") + " " + school + " " + title),
                    "applyStart": "",
                    "applyEnd": apply_end,
                    "workStart": "",
                    "workEnd": "",
                    "registered": registered,
                    "headcount": "",
                    "source": office,
                    "checkedSources": [office],
                    "sourceType": "교육지원청 개별 게시판",
                    "url": link,
                    "boardUrl": board_url,
                    "detailLinkResolved": bool(detail),
                })

        if page_table_rows == 0:
            stop_reason = "no_rows"
            break
        signature = tuple(page_row_keys)
        if signature and signature in seen_page_signatures:
            stop_reason = "repeated_page"
            break
        if signature:
            seen_page_signatures.add(signature)
        fully_dated = len(page_dates) == page_table_rows
        if fully_dated and page_dates and all(not recent_enough(d) for d in page_dates):
            consecutive_old_pages += 1
        else:
            consecutive_old_pages = 0
        if consecutive_old_pages >= 2:
            stop_reason = "retention_boundary"
            break
        page += 1

    return collected, {"url": board_url, "rawRows": raw_rows, "pagesOk": pages_ok, "explicitEmpty": explicit_empty, "error": late_error, "stopReason": stop_reason, "crossedLookback": stop_reason == "retention_boundary", "lastPage": pages_ok}


def merge_jobs(existing, additions):
    by_key = {}
    output = []

    def key_for(j):
        raw_url = j.get("url") or ""
        try:
            parsed = urlparse(raw_url)
            query = parse_qs(parsed.query)
            ntt = str(j.get("nttSn") or (query.get("nttSn") or [""])[0])
            bbs = str(j.get("bbsId") or (query.get("bbsId") or [""])[0])
            if ntt.isdigit() and bbs.isdigit():
                host = (parsed.hostname or urlparse(j.get("boardUrl") or "").hostname or "").lower()
                return f"mircms|{host}|{bbs}|{ntt}"
        except Exception:
            pass
        title = norm(j.get("title", ""))
        school = norm(j.get("school", ""))
        province = j.get("province", "")
        return province + "|" + title if len(title) >= 14 else province + "|" + title + "|" + school

    for job in existing + additions:
        key = key_for(job)
        if not key or key.endswith("|"):
            continue
        if key not in by_key:
            job["checkedSources"] = list(dict.fromkeys(job.get("checkedSources") or [job.get("source", "")]))
            by_key[key] = job
            output.append(job)
            continue
        old = by_key[key]
        sources = list(dict.fromkeys((old.get("checkedSources") or [old.get("source", "")]) + (job.get("checkedSources") or [job.get("source", "")])))
        old["checkedSources"] = [x for x in sources if x]
        if len(old["checkedSources"]) > 1:
            old["source"] = " + ".join(old["checkedSources"][:3]) + (f" 외 {len(old['checkedSources'])-3}곳" if len(old["checkedSources"]) > 3 else "")
        for field in ("region", "school", "applyEnd", "subject"):
            if not old.get(field) and job.get(field):
                old[field] = job[field]
        # Prefer a newly resolved exact MirCMS detail URL over an older list-page fallback.
        if old.get("sourceType") == "교육지원청 개별 게시판" and job.get("detailLinkResolved") and job.get("url"):
            old_url = old.get("url", "")
            old_board = old.get("boardUrl", "")
            if (not old_url) or old_url == old_board or "selectNttList.do" in old_url or old.get("detailLinkResolved") is False:
                old["url"] = job["url"]
                old["boardUrl"] = job.get("boardUrl", old_board)
                old["detailLinkResolved"] = True
        old["regions"] = list(dict.fromkeys((old.get("regions") or []) + (job.get("regions") or [])))
    output.sort(key=lambda j: (j.get("registered", ""), j.get("applyEnd", "")), reverse=True)
    return output


def main():
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    payload = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    offices = sources["gyeonggi"]["supportOffices"]
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5"})

    all_new = []
    statuses = []
    error_prefixes = {src["name"] + ":" for src in offices}
    old_errors = [e for e in payload.get("errors", []) if not any(str(e).startswith(prefix) for prefix in error_prefixes)]

    for src in offices:
        boards = list(dict.fromkeys(src.get("boardUrls", [])))
        board_meta = []
        rows = []
        for board in boards:
            found, meta = fetch_board(session, board, src)
            rows.extend(found)
            board_meta.append(meta)

        raw_total = sum(m.get("rawRows", 0) for m in board_meta)
        pages_ok = sum(m.get("pagesOk", 0) for m in board_meta)
        explicit_empty = any(m.get("explicitEmpty") for m in board_meta)
        errors = [m.get("error") for m in board_meta if m.get("error")]
        safe_stops = {"no_rows", "repeated_page", "retention_boundary"}
        traversal_complete = bool(board_meta) and all((not m.get("error")) and m.get("stopReason") in safe_stops for m in board_meta)

        if not boards:
            state, ok, message = "error", False, "실제 채용 게시판 URL 미등록"
        elif raw_total > 0 and traversal_complete:
            state, ok, message = "ok", True, f"실제 게시판 행 {raw_total}건 완전순회 · 최근 채용 {len(rows)}건"
        elif raw_total > 0:
            state, ok, message = "needs_check", False, f"게시판 행 {raw_total}건 확인했으나 pagination 종료 근거 불충분"
        elif pages_ok and explicit_empty:
            state, ok, message = "empty", True, "공식 게시판 정상 · 현재 게시물 0건"
        elif pages_ok:
            state, ok, message = "needs_check", False, "공식 페이지 접속 성공 · 채용 표 구조 추가 확인 필요"
        else:
            state, ok, message = "error", False, (errors[0] if errors else "공식 게시판 접속 실패")

        status = {
            "name": src["name"],
            "url": src.get("url", boards[0] if boards else ""),
            "boards": boards,
            "count": len(rows),
            "rawRows": raw_total,
            "ok": ok,
            "state": state,
            "message": message,
            "parser": "gyeonggi-mircms-table-v2",
            "boardHealth": board_meta,
        }
        statuses.append(status)
        all_new.extend(rows)
        if not ok:
            old_errors.append(src["name"] + ":" + message)
        print(src["name"], "state=", state, "raw=", raw_total, "jobs=", len(rows), "boards=", len(boards))

    payload.setdefault("sources", {}).setdefault("gyeonggi", {})["supportOffices"] = statuses
    payload["jobs"] = merge_jobs(payload.get("jobs", []), all_new)
    payload["errors"] = old_errors[:100]
    payload.setdefault("repair", {})["gyeonggiSupport"] = {
        "checkedAt": NOW.strftime("%Y-%m-%d %H:%M KST"),
        "parser": "gyeonggi-mircms-table-v2",
        "offices": len(statuses),
        "healthy": sum(1 for s in statuses if s["ok"]),
        "needsCheck": sum(1 for s in statuses if not s["ok"]),
        "recentJobsRead": len(all_new),
    }
    JOBS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("GYEONGGI SUPPORT REPAIR", payload["repair"]["gyeonggiSupport"])


if __name__ == "__main__":
    main()
