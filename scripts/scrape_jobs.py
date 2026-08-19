#!/usr/bin/env python3
import json
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SOURCES = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
OUT = ROOT / "jobs.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
UA = "Mozilla/5.0 (compatible; metro-edujob/2.0; public recruitment aggregator)"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6"})

GYEONGGI = SOURCES["gyeonggi"]
SEOUL = SOURCES["seoul"]

GYEONGGI_CATEGORIES = {
    "A": ("기간제/사립교원", "기간제교원"),
    "B": ("초중등시간강사", "시간강사/강사"),
    "C": ("교육공무직원", "교육공무직/기간제근로자"),
    "D": ("사립학교 교원정규채용", "정규채용"),
    "E": ("사립학교 사무직정규채용", "정규채용"),
    "F": ("자원봉사자", "자원봉사"),
}

GYEONGGI_REGIONS = [
    "수원시","성남시","용인시","고양시","화성시","안산시","평택시","파주시","김포시","남양주시",
    "부천시","안양시","시흥시","광명시","광주시","군포시","이천시","오산시","안성시","의왕시",
    "하남시","여주시","양평군","과천시","의정부시","양주시","구리시","포천시","동두천시","가평군","연천군"
]
SEOUL_REGIONS = [
    "강남구","강동구","강북구","강서구","관악구","광진구","구로구","금천구","노원구","도봉구",
    "동대문구","동작구","마포구","서대문구","서초구","성동구","성북구","송파구","양천구","영등포구",
    "용산구","은평구","종로구","중구","중랑구"
]
ALL_REGIONS = GYEONGGI_REGIONS + SEOUL_REGIONS

JOB_WORDS = re.compile(r"채용|구인|모집|기간제|시간강사|강사|교사|교원|공무직|사무직|직원|조리실무|돌봄|보육전담|봉사자")
EXCLUDE_WORDS = re.compile(r"최종\s*합격|합격자|서류\s*심사|서류전형|면접\s*대상|선정\s*결과|채용\s*결과|전형\s*결과|합격\s*공고")
DATE_RE = re.compile(r"(20\d{2})[./-]\s*(\d{1,2})[./-]\s*(\d{1,2})")


def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def norm(s):
    s = unicodedata.normalize("NFKC", clean(s)).lower()
    return re.sub(r"[^0-9a-z가-힣]+", "", s)


def date_norm(s):
    if not s:
        return ""
    m = DATE_RE.search(s)
    if not m:
        return ""
    return f"{int(m.group(1)):04d}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"


def split_period(s):
    dates = [date_norm(x.group(0)) for x in DATE_RE.finditer(s or "")]
    return (dates[0] if dates else "", dates[1] if len(dates) > 1 else "")


def guess_school_level(text):
    t = text or ""
    if "특수학교" in t or "특수학교" in t.replace(" ", ""): return "특수학교"
    if "유치원" in t or "병설유" in t or re.search(r"[가-힣]+유\b", t): return "유치원"
    if "초등학교" in t or re.search(r"[가-힣]+초\b", t): return "초등학교"
    if "중학교" in t or re.search(r"[가-힣]+중\b", t): return "중학교"
    if "고등학교" in t or re.search(r"[가-힣]+고\b", t): return "고등학교"
    if "교육지원청" in t or "교육청" in t: return "교육행정기관"
    return "기타"


def normalize_school_level(raw, school="", title=""):
    raw = clean(raw)
    guessed = guess_school_level(f"{school} {title}")
    if guessed != "기타":
        return guessed
    if "특수" in raw: return "특수학교"
    if "유치" in raw: return "유치원"
    if "초등" in raw: return "초등학교"
    if "고등" in raw: return "고등학교"
    if "중등" in raw or "중학교" in raw: return "중학교"
    if "행정" in raw or "기관" in raw: return "교육행정기관"
    return "기타"


def guess_type(text):
    t = text or ""
    if "자원봉사" in t or "봉사자" in t or "봉사인력" in t: return "자원봉사"
    if "정규교원" in t or "정규채용" in t or ("정규" in t and ("교원" in t or "사무직" in t)): return "정규채용"
    if "교육공무직" in t or "기간제근로" in t or "조리실무" in t or "돌봄전담" in t or "보육전담" in t: return "교육공무직/기간제근로자"
    if "시간강사" in t or "전임강사" in t or "강사" in t or "시간제" in t: return "시간강사/강사"
    if "기간제" in t or "계약제교원" in t or "교원" in t or "교사" in t: return "기간제교원"
    return "기타"


def find_region(text, regions=ALL_REGIONS):
    t = text or ""
    return next((x for x in regions if x in t), "")


def recent_enough(ds, days=70):
    if not ds:
        return True
    try:
        d = datetime.strptime(ds, "%Y/%m/%d").replace(tzinfo=KST)
        return d >= NOW - timedelta(days=days)
    except Exception:
        return True


def get(url, **kwargs):
    try:
        r = S.get(url, timeout=22, allow_redirects=True, **kwargs)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception as e:
        print("GET FAIL", url, type(e).__name__, str(e)[:150])
        return None


def post(url, data):
    try:
        r = S.post(url, data=data, timeout=24, allow_redirects=True)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception as e:
        print("POST FAIL", url, type(e).__name__, str(e)[:150])
        return None


# -------------------- 경기: 경기도교육청 통합 구인구직 --------------------
def parse_gyeonggi_labelled(container):
    vals = {}
    for p in container.select(".cont_btm p"):
        lab = p.select_one(".btm_tit")
        if not lab:
            continue
        key = clean(lab.get_text(" ", strip=True))
        txt = clean(p.get_text(" ", strip=True).replace(key, "", 1))
        vals[key] = txt
    return vals


def scrape_gyeonggi_central():
    central_url = GYEONGGI["central"]["url"]
    jobs, seen_ids = [], set()
    for code, (cat_name, ui_type) in GYEONGGI_CATEGORIES.items():
        print("GYEONGGI CENTRAL", code, cat_name)
        for page in range(1, 35):
            data = {
                "mi": "10502", "pbancSn": "", "currPage": str(page),
                "srchEcptDl": "Y", "srchTodayPb": "", "srchLgnNm": "",
                "srchOcptNm": cat_name, "srchOcptCd": code,
                "pageIndex": "50", "orderbyType": "reg", "searchType": "", "searchValue": "",
                "btchDlYn": "", "cndNo": "", "srchSchlSe": ""
            }
            r = post(central_url, data)
            if not r:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            rows = soup.select("div.recruit_list > ul > li")
            if not rows:
                break
            added = 0
            for li in rows:
                a = li.find("a", href=re.compile(r"goView\(['\"]?\d+"))
                if not a:
                    continue
                m = re.search(r"goView\(['\"]?(\d+)", a.get("href", ""))
                if not m:
                    continue
                pid = m.group(1)
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                added += 1
                top = [clean(x.get_text(" ", strip=True)) for x in li.select(".cont_top span")]
                school = top[0] if top else ""
                registered = next((date_norm(x) for x in top if "등록일" in x), "")
                title_el = li.select_one(".cont_tit")
                title = clean(title_el.get_text(" ", strip=True) if title_el else "")
                title = clean(re.sub(r"^(마감임박|오늘등록|NEW)\s*", "", title))
                vals = parse_gyeonggi_labelled(li)
                apply_start, apply_end = split_period(vals.get("접수기간", ""))
                work_start, work_end = split_period(vals.get("채용기간", ""))
                subject = vals.get("직무분야", "")
                headcount = re.sub(r"[^0-9명]+", "", vals.get("채용인원", ""))
                body_text = clean(li.get_text(" ", strip=True))
                region = find_region(body_text, GYEONGGI_REGIONS)
                jobs.append({
                    "id": "goe-" + pid,
                    "province": "경기",
                    "school": school,
                    "title": title,
                    "subject": subject,
                    "region": region,
                    "regions": [region] if region else [],
                    "type": ui_type,
                    "schoolLevel": normalize_school_level("", school, title),
                    "applyStart": apply_start, "applyEnd": apply_end,
                    "workStart": work_start, "workEnd": work_end,
                    "registered": registered, "headcount": headcount,
                    "source": GYEONGGI["central"]["name"],
                    "sourceType": "통합게시판",
                    "url": f"https://www.goe.go.kr/recruit/ad/func/pb/hnfpPbancInfoView.do?mi=10502&pbancSn={pid}"
                })
            if added == 0 or len(rows) < 45:
                break
            time.sleep(0.10)
    return jobs


# -------------------- 경기: 25개 교육지원청 개별 게시판 --------------------
def same_site(base, url):
    b = urlparse(base)
    u = urlparse(url)
    return (u.hostname or "").replace("www.", "") == (b.hostname or "").replace("www.", "")


def candidate_boards(base_url):
    r = get(base_url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    pages = [(r.url, soup)]
    for a in soup.find_all("a", href=True):
        txt = clean(a.get_text(" ", strip=True))
        if re.search(r"사이트맵|누리집지도|전체메뉴", txt):
            u = urljoin(r.url, a["href"])
            if same_site(r.url, u):
                rr = get(u)
                if rr:
                    pages.append((rr.url, BeautifulSoup(rr.text, "html.parser")))
                    break
    found = []
    for page_url, psoup in pages:
        for a in psoup.find_all("a", href=True):
            txt = clean(a.get_text(" ", strip=True))
            href = a.get("href", "")
            context = clean(a.parent.get_text(" ", strip=True) if a.parent else txt)
            u = urljoin(page_url, href)
            if not same_site(base_url, u):
                continue
            if "selectNttList.do" not in u and "cntntsView.do" not in u:
                continue
            if JOB_WORDS.search(txt + " " + context) or re.search(r"구인|채용|기간제|공무직", u, re.I):
                found.append(u)
    return list(dict.fromkeys(found))[:16]


def row_date(node):
    text = clean(node.get_text(" ", strip=True))
    dates = [date_norm(m.group(0)) for m in DATE_RE.finditer(text)]
    return dates[-1] if dates else ""


def scrape_gyeonggi_office(src):
    office, base, regions = src["name"], src["url"], src.get("regions", [])
    print("GYEONGGI OFFICE", office)
    boards = candidate_boards(base)
    print(" boards", len(boards))
    out, seen = [], set()
    for board in boards:
        r = get(board)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if "selectNttInfo.do" not in href:
                continue
            title = clean(a.get_text(" ", strip=True))
            if len(title) < 4 or not JOB_WORDS.search(title) or EXCLUDE_WORDS.search(title):
                continue
            url = urljoin(r.url, href)
            if url in seen:
                continue
            seen.add(url)
            parent = a.find_parent("tr") or a.find_parent("li") or a.parent
            registered = row_date(parent) if parent else ""
            if registered and not recent_enough(registered, 70):
                continue
            region = find_region(f"{title} {clean(parent.get_text(' ', strip=True) if parent else '')}", regions)
            if not region and len(regions) == 1:
                region = regions[0]
            out.append({
                "id": "goe-office-" + str(abs(hash(url))),
                "province": "경기",
                "school": office,
                "title": title,
                "subject": "",
                "region": region,
                "regions": regions,
                "type": guess_type(title),
                "schoolLevel": normalize_school_level("", "", title),
                "applyStart": "", "applyEnd": "",
                "workStart": "", "workEnd": "",
                "registered": registered, "headcount": "",
                "source": office,
                "sourceType": "교육지원청 개별 게시판",
                "url": url
            })
            if len(out) >= 150:
                break
        if len(out) >= 150:
            break
        time.sleep(0.06)
    return out


# -------------------- 서울: 서울교육일자리포털 --------------------
def parse_seoul_card(card):
    vals = {}
    for lab in card.select("p.tag_title"):
        key = clean(lab.get_text(" ", strip=True))
        nxt = lab.find_next_sibling()
        if nxt:
            vals[key] = clean(nxt.get_text(" ", strip=True))
    return vals


def scrape_seoul_central():
    base_url = SEOUL["central"]["url"]
    jobs, seen = [], set()
    print("SEOUL CENTRAL")
    for page in range(1, 60):
        params = {
            "type": "term",
            "q_currPage": str(page),
            "q_rowPerPage": "50",
            "q_sortBy": "regDt",
            "q_recClosed": "closed"
        }
        r = get(base_url, params=params)
        if not r:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("#srchDataDiv li.flex_cont")
        if not cards:
            break
        added = 0
        for li in cards:
            a = li.find("a", href=re.compile(r"BD_selectRecDetail\.do\?q_rcrtSn="))
            if not a:
                continue
            m = re.search(r"q_rcrtSn=(\d+)", a.get("href", ""))
            if not m:
                continue
            rid = m.group(1)
            if rid in seen:
                continue
            seen.add(rid)
            added += 1
            s_title = clean(li.select_one(".s_title").get_text(" ", strip=True) if li.select_one(".s_title") else "")
            school = clean(s_title.split("|")[0]) if s_title else ""
            registered = date_norm(s_title)
            title = clean(li.select_one(".list_title").get_text(" ", strip=True) if li.select_one(".list_title") else "")
            vals = parse_seoul_card(li)
            subject = vals.get("과목(담당업무)", "")
            region = find_region(vals.get("모집정보", "") + " " + title + " " + school, SEOUL_REGIONS)
            apply_start, apply_end = split_period(vals.get("접수기간", ""))
            work_start, work_end = split_period(vals.get("채용기간", ""))
            job_field = vals.get("직무분야", "")
            jobs.append({
                "id": "sen-" + rid,
                "province": "서울",
                "school": school,
                "title": title,
                "subject": subject,
                "region": region,
                "regions": [region] if region else [],
                "type": guess_type(job_field + " " + title),
                "schoolLevel": normalize_school_level("", school, title),
                "applyStart": apply_start, "applyEnd": apply_end,
                "workStart": work_start, "workEnd": work_end,
                "registered": registered,
                "headcount": re.sub(r"[^0-9명]+", "", vals.get("채용인원", "")),
                "source": SEOUL["central"]["name"],
                "sourceType": "통합게시판",
                "url": urljoin(r.url, a.get("href", ""))
            })
        if added == 0:
            break
        time.sleep(0.09)
    return jobs


# -------------------- 서울: 11개 교육지원청 개별 구인 게시판 --------------------
def normalize_header(s):
    return re.sub(r"\s+", "", clean(s))


def first_of(d, keys):
    for k in keys:
        if k in d and d[k]:
            return d[k]
    return ""


def scrape_seoul_office(src):
    office = src["name"]
    board = src.get("boardUrl") or urljoin(src["url"], "/FUS/JO/JOL11.do")
    regions = src.get("regions", [])
    print("SEOUL OFFICE", office)
    out, seen = [], set()
    for page in range(1, 80):
        r = post(board, {
            "pageIndex": str(page),
            "searchPartPosition": "",
            "searchCondition": "lesson",
            "searchKeyword": ""
        })
        if not r:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        target = None
        headers = []
        for table in soup.find_all("table"):
            hs = [normalize_header(x.get_text(" ", strip=True)) for x in table.find_all("th")]
            if "마감일" in hs and ("직종" in hs or "직종" in "".join(hs)):
                target = table
                headers = hs
                break
        if not target:
            break
        rows = target.find_all("tr")
        page_items = 0
        recent_items = 0
        for tr in rows:
            tds = tr.find_all("td")
            if not tds:
                continue
            a = tr.find("a", href=re.compile(r"fncDetailView"))
            if not a:
                continue
            m = re.search(r"fncDetailView\(['\"]?(\d+)", a.get("href", ""))
            seq = m.group(1) if m else ""
            if not seq or seq in seen:
                continue
            seen.add(seq)
            page_items += 1
            vals = {}
            for i, td in enumerate(tds):
                if i < len(headers):
                    vals[headers[i]] = clean(td.get_text(" ", strip=True))
            title = clean(a.get_text(" ", strip=True))
            if EXCLUDE_WORDS.search(title):
                continue
            school = first_of(vals, ["학교명", "기관명"])
            raw_level = first_of(vals, ["학교급별", "학교급"])
            raw_type = first_of(vals, ["직종"])
            registered = date_norm(first_of(vals, ["등록일", "작성일"]))
            apply_end = date_norm(first_of(vals, ["마감일"]))
            if registered and not recent_enough(registered, 70) and not (apply_end and recent_enough(apply_end, 0)):
                continue
            recent_items += 1
            region = first_of(vals, ["지역"])
            if region in ("전체", "서울", "서울시"):
                region = ""
            if region not in SEOUL_REGIONS:
                region = find_region(f"{region} {title} {school}", regions)
            if not region and len(regions) == 1:
                region = regions[0]
            subject = first_of(vals, ["분야(과목)", "분야", "과목"])
            job_type = guess_type(raw_type + " " + title)
            detail_url = urljoin(board, f"/FUS/JO/JOV11.do?job_seq={seq}")
            out.append({
                "id": f"sen-office-{urlparse(board).hostname}-{seq}",
                "province": "서울",
                "school": school or office,
                "title": title,
                "subject": subject,
                "region": region,
                "regions": regions,
                "type": job_type,
                "schoolLevel": normalize_school_level(raw_level, school, title),
                "applyStart": registered, "applyEnd": apply_end,
                "workStart": "", "workEnd": "",
                "registered": registered, "headcount": "",
                "source": office,
                "sourceType": "교육지원청 개별 게시판",
                "url": detail_url,
                "boardUrl": board
            })
        if page_items == 0:
            break
        # 등록일 역순 게시판이므로 최근 70일 자료가 더 이상 없으면 종료
        if recent_items == 0 and page > 2:
            break
        time.sleep(0.06)
    return out


def dedupe(jobs):
    # 통합 게시판을 우선하고, 같은 시도 안에서 같은 제목의 개별 게시판 중복을 제거한다.
    jobs = sorted(jobs, key=lambda j: 0 if j.get("sourceType") == "통합게시판" else 1)
    out, seen = [], set()
    for j in jobs:
        province = j.get("province", "")
        titlekey = norm(j.get("title", ""))
        schoolkey = norm(j.get("school", ""))
        key = province + "|" + (titlekey if len(titlekey) >= 12 else titlekey + "|" + schoolkey)
        if not titlekey or key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out


def main():
    all_jobs = []
    errors = []
    status = {"gyeonggi": {"supportOffices": []}, "seoul": {"supportOffices": []}}

    try:
        rows = scrape_gyeonggi_central()
        all_jobs.extend(rows)
        status["gyeonggi"]["central"] = {"name": GYEONGGI["central"]["name"], "url": GYEONGGI["central"]["url"], "count": len(rows), "ok": True}
    except Exception as e:
        errors.append("경기도교육청:" + repr(e))
        status["gyeonggi"]["central"] = {"name": GYEONGGI["central"]["name"], "url": GYEONGGI["central"]["url"], "count": 0, "ok": False}

    for src in GYEONGGI["supportOffices"]:
        try:
            rows = scrape_gyeonggi_office(src)
            all_jobs.extend(rows)
            status["gyeonggi"]["supportOffices"].append({"name": src["name"], "url": src["url"], "count": len(rows), "ok": True})
        except Exception as e:
            errors.append(src["name"] + ":" + repr(e))
            status["gyeonggi"]["supportOffices"].append({"name": src["name"], "url": src["url"], "count": 0, "ok": False})

    try:
        rows = scrape_seoul_central()
        all_jobs.extend(rows)
        status["seoul"]["central"] = {"name": SEOUL["central"]["name"], "url": SEOUL["central"]["url"], "count": len(rows), "ok": True}
    except Exception as e:
        errors.append("서울교육일자리포털:" + repr(e))
        status["seoul"]["central"] = {"name": SEOUL["central"]["name"], "url": SEOUL["central"]["url"], "count": 0, "ok": False}

    for src in SEOUL["supportOffices"]:
        try:
            rows = scrape_seoul_office(src)
            all_jobs.extend(rows)
            status["seoul"]["supportOffices"].append({"name": src["name"], "url": src.get("boardUrl", src["url"]), "count": len(rows), "ok": True})
        except Exception as e:
            errors.append(src["name"] + ":" + repr(e))
            status["seoul"]["supportOffices"].append({"name": src["name"], "url": src.get("boardUrl", src["url"]), "count": 0, "ok": False})

    all_jobs = dedupe(all_jobs)
    all_jobs.sort(key=lambda j: (j.get("registered", ""), j.get("applyEnd", "")), reverse=True)
    payload = {
        "updatedAt": NOW.strftime("%Y-%m-%d %H:%M KST"),
        "officialSourceCount": 2 + len(GYEONGGI["supportOffices"]) + len(SEOUL["supportOffices"]),
        "sources": status,
        "errors": errors[:50],
        "jobs": all_jobs
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("TOTAL", len(all_jobs), "official sources", payload["officialSourceCount"], "errors", len(errors))


if __name__ == "__main__":
    main()
