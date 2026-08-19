#!/usr/bin/env python3
"""Second-pass support-office crawler focused on completeness, not merely parser success.

Runs after the primary collector. It re-crawls every configured/cached official support-office
board with dynamic pagination, stops on repeated pages or when the 90-day look-back is fully
crossed, appends genuinely missed postings, and records coverage diagnostics in jobs.json.
"""
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SOURCES = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
CACHE_PATH = ROOT / "board_cache.json"
JOBS_PATH = ROOT / "jobs.json"
CACHE = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
PAYLOAD = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
JOBS = PAYLOAD.get("jobs", [])

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
LOOKBACK_DAYS = 90
MAX_PAGES = 80
UA = "Mozilla/5.0 (compatible; metro-edujob-completeness/1.0; public recruitment aggregator)"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6"})

DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")
JOB_WORDS = re.compile(r"채용|구인|모집|기간제|계약제|시간강사|강사|교사|교원|공무직|사무직|근로자|조리|돌봄|보육|봉사|튜터|안전지킴이|외부강사")
EXCLUDE_WORDS = re.compile(r"최종\s*합격|합격자|서류\s*심사|서류전형|면접\s*대상|선정\s*결과|채용\s*결과|전형\s*결과|합격\s*공고|인사발령")


def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def date_norm(s):
    m = DATE_RE.search(s or "")
    if not m:
        return ""
    return f"{int(m.group(1)):04d}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"


def all_dates(s):
    return [date_norm(m.group(0)) for m in DATE_RE.finditer(s or "")]


def recent(ds):
    if not ds:
        return True
    try:
        d = datetime.strptime(ds, "%Y/%m/%d").replace(tzinfo=KST)
        return d >= NOW - timedelta(days=LOOKBACK_DAYS)
    except Exception:
        return True


def definitely_old(ds):
    if not ds:
        return False
    try:
        d = datetime.strptime(ds, "%Y/%m/%d").replace(tzinfo=KST)
        return d < NOW - timedelta(days=LOOKBACK_DAYS)
    except Exception:
        return False


def get(url, **kwargs):
    try:
        r = S.get(url, timeout=20, allow_redirects=True, **kwargs)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception:
        return None


def post(url, data):
    try:
        r = S.post(url, data=data, timeout=22, allow_redirects=True)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception:
        return None


def with_query(url, **changes):
    p = urlparse(url)
    q = parse_qs(p.query, keep_blank_values=True)
    for k, v in changes.items():
        q[k] = [str(v)]
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q, doseq=True), p.fragment))


def headers(table):
    tr = table.find("tr")
    return [clean(x.get_text(" ", strip=True)) for x in tr.find_all("th")] if tr else []


def first_of(vals, keys):
    for key in keys:
        for actual, value in vals.items():
            if key in actual and value:
                return value
    return ""


def school_from_title(title):
    m = re.match(r"\s*[\[(]([^\])]+)[\])]", title or "")
    if m:
        return clean(m.group(1))
    m = re.search(r"([가-힣A-Za-z0-9·]+(?:유치원|초등학교|중학교|고등학교|학교))", title or "")
    return clean(m.group(1)) if m else ""


def detail_from_anchor(board, a):
    href = a.get("href", "") or ""
    if "selectNttInfo.do" in href:
        return urljoin(board, href)
    data_id = clean(str(a.get("data-id", "")))
    raw = href + " " + (a.get("onclick", "") or "")
    ntt = data_id if re.fullmatch(r"\d{4,12}", data_id) else ""
    if not ntt:
        m = re.search(r"nttSn\s*[=:,'\"() ]+\s*(\d{4,})", raw, re.I)
        if not m:
            m = re.search(r"(?:nttView|selectNttInfo|goView)\D+(\d{4,})", raw, re.I)
        ntt = m.group(1) if m else ""
    if not ntt:
        return ""
    p = urlparse(board)
    q = parse_qs(p.query)
    bbs = (q.get("bbsId") or [""])[0]
    if not bbs:
        return ""
    params = {"bbsId": bbs, "nttSn": ntt}
    for k in ("mi", "clasHmpgId"):
        if q.get(k):
            params[k] = q[k][0]
    path = p.path.replace("selectNttList.do", "selectNttInfo.do")
    return urlunparse((p.scheme, p.netloc, path, "", urlencode(params), ""))


def row_vals(tr, hs):
    vals = {}
    for i, td in enumerate(tr.find_all("td")):
        if i < len(hs) and hs[i]:
            vals[hs[i]] = clean(td.get_text(" ", strip=True))
    return vals


def gyeonggi_board(board, src):
    out = []
    seen_details = set()
    seen_page_sigs = set()
    pages = 0
    raw_rows = 0
    repeat = False
    access_error = False
    explicit_empty = False
    crossed_old = False
    consecutive_old_pages = 0

    for page in range(1, MAX_PAGES + 1):
        r = get(with_query(board, currPage=page))
        if not r:
            access_error = page == 1
            break
        soup = BeautifulSoup(r.text, "html.parser")
        txt = clean(soup.get_text(" ", strip=True))
        if re.search(r"전체\s*0\s*건|총\s*0\s*건|등록된\s*게시물이\s*없", txt):
            explicit_empty = True

        page_items = []
        page_dates = []
        for table in soup.find_all("table"):
            hs = headers(table)
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if not tds:
                    continue
                pick = None
                detail = ""
                for a in tr.find_all("a"):
                    d = detail_from_anchor(r.url, a)
                    title = clean(a.get_text(" ", strip=True))
                    if d and len(title) >= 3:
                        pick, detail = a, d
                        break
                if not pick or detail in seen_details:
                    continue
                vals = row_vals(tr, hs)
                row_text = clean(tr.get_text(" ", strip=True))
                registered = date_norm(first_of(vals, ["등록일", "작성일"]))
                if not registered:
                    ds = all_dates(row_text)
                    registered = ds[-1] if ds else ""
                title = clean(pick.get_text(" ", strip=True)).replace("새로운 글", "").strip()
                page_items.append((detail, title, registered, vals, row_text))
                if registered:
                    page_dates.append(registered)

        sig = tuple(x[0] for x in page_items)
        if sig and sig in seen_page_sigs:
            repeat = True
            break
        if sig:
            seen_page_sigs.add(sig)
        pages += 1
        if not page_items:
            break

        raw_rows += len(page_items)
        recent_on_page = 0
        for detail, title, registered, vals, row_text in page_items:
            seen_details.add(detail)
            if registered and not recent(registered):
                continue
            if EXCLUDE_WORDS.search(title):
                continue
            if not JOB_WORDS.search(title):
                continue
            recent_on_page += 1
            school = first_of(vals, ["학교명", "기관명"]) or school_from_title(title) or src["name"]
            region = first_of(vals, ["지역"])
            if region not in src.get("regions", []):
                region = next((x for x in src.get("regions", []) if x in f"{row_text} {school} {title}"), "")
            out.append({
                "id": "goe-complete-" + hashlib.sha1(detail.encode()).hexdigest()[:18],
                "province": "경기", "school": school, "title": title,
                "subject": first_of(vals, ["과목", "분야"]), "region": region,
                "regions": src.get("regions", []), "type": "기타", "schoolLevel": "기타",
                "applyStart": "", "applyEnd": date_norm(first_of(vals, ["접수마감일", "마감일"])),
                "workStart": "", "workEnd": "", "registered": registered, "headcount": "",
                "source": src["name"], "checkedSources": [src["name"]],
                "sourceType": "교육지원청 개별 게시판", "url": detail, "boardUrl": board,
                "detailLinkResolved": True,
            })

        if page_dates and all(definitely_old(x) for x in page_dates):
            consecutive_old_pages += 1
        else:
            consecutive_old_pages = 0
        if consecutive_old_pages >= 2:
            crossed_old = True
            break
        if recent_on_page == 0 and page >= 3 and page_dates and max(page_dates) < (NOW - timedelta(days=LOOKBACK_DAYS)).strftime("%Y/%m/%d"):
            crossed_old = True
            break
        time.sleep(0.03)

    complete = (not access_error) and (crossed_old or (pages > 0 and not repeat and pages < MAX_PAGES))
    return out, {
        "url": board, "pagesScanned": pages, "rawRows": raw_rows,
        "recentRows": len(out), "paginationRepeated": repeat, "accessError": access_error,
        "explicitEmpty": explicit_empty, "crossedLookback": crossed_old,
        "coverageComplete": complete,
    }


def seoul_seq(tr):
    html = str(tr)
    for pat in (r"fncDetailView\(['\"]?(\d+)", r"job_seq[=,'\"() ]+(\d+)", r"JOV11\.do[^\d]+(\d+)"):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)
    return ""


def seoul_page(board, page, prefer_post=False):
    params = {"pageIndex": page}
    if prefer_post:
        return post(board, {"pageIndex": str(page), "searchPartPosition": "", "searchCondition": "lesson", "searchKeyword": ""})
    return get(board, params=params)


def seoul_board(src):
    board = src["boardUrl"]
    out = []
    seen = set()
    seen_page_sigs = set()
    pages = 0
    raw_rows = 0
    repeat = False
    access_error = False
    explicit_empty = False
    got_table = False
    crossed_old = False
    consecutive_old_pages = 0
    use_post = False

    for page in range(1, MAX_PAGES + 1):
        r = seoul_page(board, page, use_post)
        if not r and not use_post:
            use_post = True
            r = seoul_page(board, page, True)
        if not r:
            access_error = page == 1
            break
        soup = BeautifulSoup(r.text, "html.parser")
        txt = clean(soup.get_text(" ", strip=True))
        if re.search(r"총\s*0\s*건|전체\s*0\s*건|데이터가\s*없습니다|조회된\s*데이터가\s*없|등록된\s*자료가\s*없", txt):
            explicit_empty = True

        items = []
        dates = []
        for table in soup.find_all("table"):
            hs = headers(table)
            if not hs:
                continue
            if not any(any(k in h for k in ("마감", "직종", "학교", "구분", "대상", "분야", "등록일", "작성자")) for h in hs):
                continue
            got_table = True
            for tr in table.find_all("tr"):
                if not tr.find_all("td"):
                    continue
                seq = seoul_seq(tr)
                if not seq:
                    continue
                vals = row_vals(tr, hs)
                anchors = [a for a in tr.find_all("a") if clean(a.get_text(" ", strip=True))]
                title = clean(max((a.get_text(" ", strip=True) for a in anchors), key=len, default=""))
                if not title:
                    title = first_of(vals, ["제목", "공고명", "분야1", "분야"])
                registered = date_norm(first_of(vals, ["등록일", "작성일"]))
                if not registered:
                    ds = all_dates(clean(tr.get_text(" ", strip=True)))
                    registered = ds[-1] if ds else ""
                items.append((seq, title, registered, vals))
                if registered:
                    dates.append(registered)

        sig = tuple(x[0] for x in items)
        if sig and sig in seen_page_sigs:
            if not use_post:
                # Some Seoul boards ignore GET pageIndex but paginate correctly on POST.
                use_post = True
                r2 = seoul_page(board, page, True)
                if r2:
                    soup2 = BeautifulSoup(r2.text, "html.parser")
                    post_items = []
                    for table in soup2.find_all("table"):
                        hs = headers(table)
                        for tr in table.find_all("tr"):
                            seq = seoul_seq(tr)
                            if seq:
                                post_items.append(seq)
                    post_sig = tuple(post_items)
                    if post_sig and post_sig not in seen_page_sigs:
                        continue
            repeat = True
            break
        if sig:
            seen_page_sigs.add(sig)
        pages += 1
        if not items:
            break
        raw_rows += len(items)

        for seq, title, registered, vals in items:
            if seq in seen:
                continue
            seen.add(seq)
            if registered and not recent(registered):
                continue
            if len(title) < 3 or EXCLUDE_WORDS.search(title):
                continue
            school = first_of(vals, ["학교명", "기관명", "작성자"]) or school_from_title(title) or src["name"]
            open_url = urljoin(board, "/FUS/JO/JOV11.do")
            out.append({
                "id": f"sen-complete-{urlparse(board).hostname}-{seq}", "province": "서울",
                "school": school, "title": title, "subject": " / ".join(x for x in (first_of(vals,["분야1"]), first_of(vals,["분야2"])) if x),
                "region": next((x for x in src.get("regions", []) if x in f"{title} {school}"), ""),
                "regions": src.get("regions", []), "type": "기타", "schoolLevel": "기타",
                "applyStart": registered, "applyEnd": date_norm(first_of(vals, ["마감일", "접수마감일"])),
                "workStart": "", "workEnd": "", "registered": registered, "headcount": "",
                "source": src["name"], "checkedSources": [src["name"]], "sourceType": "교육지원청 개별 게시판",
                "url": f"{open_url}?job_seq={seq}", "boardUrl": board,
                "openMethod": "POST", "openUrl": open_url, "openParams": {"job_seq": seq},
            })

        if dates and all(definitely_old(x) for x in dates):
            consecutive_old_pages += 1
        else:
            consecutive_old_pages = 0
        if consecutive_old_pages >= 2:
            crossed_old = True
            break
        time.sleep(0.03)

    complete = (not access_error) and (crossed_old or (pages > 0 and not repeat and pages < MAX_PAGES))
    return out, {
        "url": board, "pagesScanned": pages, "rawRows": raw_rows, "recentRows": len(out),
        "paginationRepeated": repeat, "accessError": access_error, "explicitEmpty": explicit_empty,
        "gotTable": got_table, "crossedLookback": crossed_old, "coverageComplete": complete,
        "paginationMethod": "POST" if use_post else "GET",
    }


def key(j):
    title = re.sub(r"[^0-9a-z가-힣]+", "", clean(j.get("title", "")).lower())
    school = re.sub(r"[^0-9a-z가-힣]+", "", clean(j.get("school", "")).lower())
    province = j.get("province", "")
    return province + "|" + title + ("" if len(title) >= 14 else "|" + school)


def merge(additions):
    by = {key(j): j for j in JOBS if key(j)}
    added = 0
    for j in additions:
        k = key(j)
        if not k:
            continue
        old = by.get(k)
        if old is None:
            JOBS.append(j)
            by[k] = j
            added += 1
            continue
        old_sources = old.get("checkedSources") or [old.get("source", "")]
        for s in j.get("checkedSources") or [j.get("source", "")]:
            if s and s not in old_sources:
                old_sources.append(s)
        old["checkedSources"] = [x for x in old_sources if x]
        # If the existing support-office record lacks an exact detail URL, prefer the complete pass.
        if old.get("sourceType") == "교육지원청 개별 게시판" and j.get("url"):
            raw = old.get("url", "")
            if not raw or "selectNttList.do" in raw:
                for f in ("url", "boardUrl", "openMethod", "openUrl", "openParams", "detailLinkResolved"):
                    if j.get(f) is not None:
                        old[f] = j.get(f)
    return added


def status_from_board(name, url, rows, metas):
    raw = sum(m.get("rawRows", 0) for m in metas)
    pages = sum(m.get("pagesScanned", 0) for m in metas)
    any_access = any(m.get("accessError") for m in metas)
    any_repeat = any(m.get("paginationRepeated") for m in metas)
    complete = bool(metas) and all(m.get("coverageComplete") for m in metas)
    genuine_empty = raw == 0 and bool(metas) and all(m.get("explicitEmpty") for m in metas)
    if genuine_empty:
        state, ok, msg = "empty", True, "공식 게시판 정상 · 최근 공고 0건"
    elif any_access and raw == 0:
        state, ok, msg = "error", False, "공식 게시판 접근 실패"
    elif any_repeat:
        state, ok, msg = "warning", False, "페이지 매개변수 반복 감지 · 완전수집 확인 필요"
    elif complete:
        state, ok, msg = "complete", True, f"최근 {LOOKBACK_DAYS}일 범위 완전수집"
    elif raw > 0:
        state, ok, msg = "warning", False, "일부 공고는 수집했으나 최근 범위 완전수집 미확인"
    else:
        state, ok, msg = "warning", False, "게시판 구조/범위 추가 확인 필요"
    return {
        "name": name, "url": url, "boards": [m["url"] for m in metas], "count": len(rows),
        "rawRows": raw, "pagesScanned": pages, "ok": ok, "state": state, "message": msg,
        "coverageComplete": complete, "boardHealth": metas,
    }


def main():
    additions = []
    gg_status = []
    seoul_status = []

    for src in SOURCES["gyeonggi"]["supportOffices"]:
        boards = []
        for u in list(src.get("boardUrls", [])) + list(CACHE.get(src["name"], [])):
            if u and u not in boards:
                boards.append(u)
        rows = []
        metas = []
        for board in boards:
            found, meta = gyeonggi_board(board, src)
            rows.extend(found)
            metas.append(meta)
        additions.extend(rows)
        gg_status.append(status_from_board(src["name"], src.get("url", boards[0] if boards else ""), rows, metas))

    for src in SOURCES["seoul"]["supportOffices"]:
        rows, meta = seoul_board(src)
        additions.extend(rows)
        seoul_status.append(status_from_board(src["name"], src.get("boardUrl", ""), rows, [meta]))

    added = merge(additions)
    PAYLOAD.setdefault("sources", {}).setdefault("gyeonggi", {})["supportOffices"] = gg_status
    PAYLOAD.setdefault("sources", {}).setdefault("seoul", {})["supportOffices"] = seoul_status
    PAYLOAD["supportCompleteness"] = {
        "lookbackDays": LOOKBACK_DAYS,
        "newlyRecoveredJobs": added,
        "gyeonggiComplete": sum(1 for x in gg_status if x.get("coverageComplete")),
        "seoulComplete": sum(1 for x in seoul_status if x.get("coverageComplete")),
        "gyeonggiTotal": len(gg_status), "seoulTotal": len(seoul_status),
        "warnings": [x["name"] for x in gg_status + seoul_status if not x.get("ok")],
    }
    JOBS.sort(key=lambda j: (j.get("registered", ""), j.get("applyEnd", "")), reverse=True)
    PAYLOAD["jobs"] = JOBS
    JOBS_PATH.write_text(json.dumps(PAYLOAD, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(PAYLOAD["supportCompleteness"], ensure_ascii=False))


if __name__ == "__main__":
    main()
