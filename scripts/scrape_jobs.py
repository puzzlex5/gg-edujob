#!/usr/bin/env python3
import json
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SOURCES = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
OUT = ROOT / "jobs.json"
CACHE_FILE = ROOT / "board_cache.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
UA = "Mozilla/5.0 (compatible; metro-edujob/3.0; public recruitment aggregator)"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6"})

GYEONGGI = SOURCES["gyeonggi"]
SEOUL = SOURCES["seoul"]
CACHE = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}

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
DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")
JOB_WORDS = re.compile(r"채용|구인|모집|기간제|계약제|시간강사|강사|교사|교원|공무직|사무직|근로자|조리|돌봄|보육|봉사|튜터|안전지킴이|외부강사")
EXCLUDE_WORDS = re.compile(r"최종\s*합격|합격자|서류\s*심사|서류전형|면접\s*대상|선정\s*결과|채용\s*결과|전형\s*결과|합격\s*공고|인사발령")
BOARD_WORDS = re.compile(r"구인|채용정보|채용공고|모집공고|기간제교사|기간제교원|교육공무직")
BOARD_EXCLUDE = re.compile(r"구직|인력풀|합격|인사정보|인사발령|공지사항|자료실")


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


def all_dates(s):
    return [date_norm(m.group(0)) for m in DATE_RE.finditer(s or "")]


def split_period(s):
    ds = all_dates(s)
    return (ds[0] if ds else "", ds[1] if len(ds) > 1 else "")


def recent_enough(ds, days=90):
    if not ds:
        return True
    try:
        d = datetime.strptime(ds, "%Y/%m/%d").replace(tzinfo=KST)
        return d >= NOW - timedelta(days=days)
    except Exception:
        return True


def guess_school_level(text):
    t = text or ""
    if "특수학교" in t: return "특수학교"
    if "유치원" in t or "병설유" in t: return "유치원"
    if "초등학교" in t or re.search(r"[가-힣]+초\b", t): return "초등학교"
    if "중학교" in t or re.search(r"[가-힣]+중\b", t): return "중학교"
    if "고등학교" in t or re.search(r"[가-힣]+고\b", t): return "고등학교"
    if "교육지원청" in t or "교육청" in t: return "교육행정기관"
    return "기타"


def normalize_school_level(raw, school="", title=""):
    guessed = guess_school_level(f"{school} {title}")
    if guessed != "기타": return guessed
    raw = clean(raw)
    if "특수" in raw: return "특수학교"
    if "유치" in raw: return "유치원"
    if "초등" in raw: return "초등학교"
    if "중등" in raw or "중학교" in raw: return "중학교"
    if "고등" in raw: return "고등학교"
    if "행정" in raw or "기관" in raw: return "교육행정기관"
    return "기타"


def guess_type(text):
    t = text or ""
    if "교육공무직" in t or "기간제근로" in t or "대체근로" in t or "조리실무" in t or "조리원" in t or "돌봄전담" in t or "보육전담" in t:
        return "교육공무직/기간제근로자"
    if "자원봉사" in t or "봉사자" in t or "안전지킴이" in t:
        return "자원봉사"
    if "정규교원" in t or "정규채용" in t or ("정규" in t and ("교원" in t or "사무직" in t)):
        return "정규채용"
    if "시간강사" in t or "전임강사" in t or "외부강사" in t or "강사" in t or "튜터" in t or "시간제" in t:
        return "시간강사/강사"
    if "기간제" in t or "계약제교원" in t or "교원" in t or "교사" in t:
        return "기간제교원"
    return "기타"


def find_region(text, regions=ALL_REGIONS):
    t = text or ""
    return next((x for x in regions if x in t), "")


def get(url, **kwargs):
    try:
        r = S.get(url, timeout=18, allow_redirects=True, **kwargs)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1": r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception as e:
        print("GET FAIL", url, type(e).__name__, str(e)[:120])
        return None


def post(url, data):
    try:
        r = S.post(url, data=data, timeout=20, allow_redirects=True)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1": r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception as e:
        print("POST FAIL", url, type(e).__name__, str(e)[:120])
        return None


def with_query(url, **changes):
    p = urlparse(url)
    q = parse_qs(p.query, keep_blank_values=True)
    for k, v in changes.items(): q[k] = [str(v)]
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q, doseq=True), p.fragment))


def same_host(a, b):
    def h(u): return (urlparse(u).hostname or "").lower().removeprefix("www.")
    return h(a) == h(b)


def first_of(d, keys):
    for k in keys:
        for actual, val in d.items():
            if k in actual and val:
                return val
    return ""


# -------------------- 경기 중앙 --------------------
def parse_gyeonggi_labelled(container):
    vals = {}
    for p in container.select(".cont_btm p"):
        lab = p.select_one(".btm_tit")
        if not lab: continue
        key = clean(lab.get_text(" ", strip=True))
        vals[key] = clean(p.get_text(" ", strip=True).replace(key, "", 1))
    return vals


def scrape_gyeonggi_central():
    central_url = GYEONGGI["central"]["url"]
    jobs, seen_ids = [], set()
    for code, (cat_name, ui_type) in GYEONGGI_CATEGORIES.items():
        print("GYEONGGI CENTRAL", code, cat_name)
        for page in range(1, 35):
            data = {
                "mi":"10502","pbancSn":"","currPage":str(page),"srchEcptDl":"Y","srchTodayPb":"","srchLgnNm":"",
                "srchOcptNm":cat_name,"srchOcptCd":code,"pageIndex":"50","orderbyType":"reg","searchType":"","searchValue":"",
                "btchDlYn":"","cndNo":"","srchSchlSe":""
            }
            r = post(central_url, data)
            if not r: break
            soup = BeautifulSoup(r.text, "html.parser")
            rows = soup.select("div.recruit_list > ul > li")
            if not rows: break
            added = 0
            for li in rows:
                a = li.find("a", href=re.compile(r"goView\(['\"]?\d+"))
                if not a: continue
                m = re.search(r"goView\(['\"]?(\d+)", a.get("href", ""))
                if not m: continue
                pid = m.group(1)
                if pid in seen_ids: continue
                seen_ids.add(pid); added += 1
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
                body_text = clean(li.get_text(" ", strip=True))
                region = find_region(body_text, GYEONGGI_REGIONS)
                jobs.append({
                    "id":"goe-"+pid,"province":"경기","school":school,"title":title,"subject":subject,
                    "region":region,"regions":[region] if region else [],"type":ui_type,
                    "schoolLevel":normalize_school_level("",school,title),"applyStart":apply_start,"applyEnd":apply_end,
                    "workStart":work_start,"workEnd":work_end,"registered":registered,
                    "headcount":re.sub(r"[^0-9명]+","",vals.get("채용인원","")),
                    "source":GYEONGGI["central"]["name"],"checkedSources":[GYEONGGI["central"]["name"]],"sourceType":"통합게시판",
                    "url":f"https://www.goe.go.kr/recruit/ad/func/pb/hnfpPbancInfoView.do?mi=10502&pbancSn={pid}"
                })
            if added == 0 or len(rows) < 45: break
            time.sleep(.05)
    return jobs


# -------------------- 경기 교육지원청: 실제 게시판 검색 + 캐시 --------------------
def derive_site_segment(resolved_url):
    parts = [x for x in urlparse(resolved_url).path.split("/") if x]
    if parts and parts[0] not in ("main.do", "index.do"): return parts[0]
    host = (urlparse(resolved_url).hostname or "").removeprefix("www.")
    return host.split(".")[0]


def discover_gyeonggi_boards(src):
    office = src["name"]
    seeds = list(src.get("boardUrls", [])) + list(CACHE.get(office, []))
    found = []
    for u in seeds:
        if u and u not in found: found.append(u)
    if not src.get("autoDiscover", True): return found

    home = get(src["url"])
    if not home: return found
    seg = derive_site_segment(home.url)
    pages = [(home.url, BeautifulSoup(home.text, "html.parser"))]
    for candidate in [urljoin(home.url, f"/{seg}/sitemap.do"), urljoin(home.url, f"/{seg}/main.do")]:
        rr = get(candidate)
        if rr:
            pages.append((rr.url, BeautifulSoup(rr.text, "html.parser")))
    for page_url, soup in pages:
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if "selectNttList.do" not in href: continue
            u = urljoin(page_url, href)
            if not same_host(home.url, u): continue
            txt = clean(a.get_text(" ", strip=True))
            parent_txt = clean(a.parent.get_text(" ", strip=True) if a.parent else txt)
            label = f"{txt} {parent_txt}"
            if BOARD_WORDS.search(label) and not (BOARD_EXCLUDE.search(label) and not re.search(r"채용|구인", txt)):
                if u not in found: found.append(u)
    # 같은 bbsId/mi의 중복 URL 정리
    uniq, seen = [], set()
    for u in found:
        p = urlparse(u); q = parse_qs(p.query)
        key = (p.hostname, p.path, (q.get("bbsId") or [""])[0], (q.get("mi") or [""])[0], (q.get("srch_aditCol1") or [""])[0])
        if key in seen: continue
        seen.add(key); uniq.append(u)
    CACHE[office] = uniq[:12]
    return uniq[:12]


def table_headers(table):
    tr = table.find("tr")
    if not tr: return []
    return [clean(x.get_text(" ", strip=True)) for x in tr.find_all("th")]


def detail_from_anchor(board, a):
    href = a.get("href", "") or ""
    onclick = a.get("onclick", "") or ""
    if "selectNttInfo.do" in href: return urljoin(board, href)
    raw = href + " " + onclick
    m = re.search(r"nttSn\s*[=:,'\"() ]+\s*(\d{4,})", raw)
    if not m: m = re.search(r"(?:nttView|selectNttInfo|goView)\D+(\d{4,})", raw)
    if not m: return ""
    p = urlparse(board); q = parse_qs(p.query)
    params = {}
    for k in ("bbsId","mi","clasHmpgId"):
        if q.get(k): params[k] = q[k][0]
    params["nttSn"] = m.group(1)
    path = p.path.replace("selectNttList.do", "selectNttInfo.do")
    return urlunparse((p.scheme,p.netloc,path,"",urlencode(params),""))


def school_from_title(title):
    m = re.match(r"\s*[\[(]([^\])]+)[\])]", title or "")
    if m: return clean(m.group(1))
    m = re.search(r"([가-힣A-Za-z0-9·]+(?:유치원|초등학교|중학교|고등학교|학교))", title or "")
    return clean(m.group(1)) if m else ""


def scrape_mircms_board(board, src):
    office, regions = src["name"], src.get("regions", [])
    out, seen_urls = [], set()
    raw_rows = 0; explicit_empty = False; pages_scanned = 0
    for page in range(1, 8):
        u = with_query(board, currPage=page)
        r = get(u)
        if not r: break
        pages_scanned += 1
        soup = BeautifulSoup(r.text, "html.parser")
        page_text = clean(soup.get_text(" ", strip=True))
        if re.search(r"전체\s*0\s*건|등록된\s*게시물이\s*없", page_text): explicit_empty = True
        page_candidates = 0; page_recent = 0
        for table in soup.find_all("table"):
            headers = table_headers(table)
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if not tds: continue
                anchors = tr.find_all("a")
                pick = None; detail = ""
                for a in anchors:
                    d = detail_from_anchor(r.url, a)
                    if d:
                        title_txt = clean(a.get_text(" ", strip=True))
                        if len(title_txt) >= 3:
                            pick, detail = a, d; break
                if not pick or detail in seen_urls: continue
                title = clean(pick.get_text(" ", strip=True)).replace("새로운 글", "").strip()
                if len(title) < 3: continue
                raw_rows += 1; page_candidates += 1; seen_urls.add(detail)
                vals = {}
                for i, td in enumerate(tds):
                    if i < len(headers) and headers[i]: vals[headers[i]] = clean(td.get_text(" ", strip=True))
                row_text = clean(tr.get_text(" ", strip=True))
                registered = date_norm(first_of(vals,["등록일","작성일"]))
                if not registered:
                    ds = all_dates(row_text); registered = ds[-1] if ds else ""
                if registered and not recent_enough(registered, 90): continue
                if EXCLUDE_WORDS.search(title): continue
                # 명확한 채용게시판이면 키워드가 약해도 허용, 그 외는 채용키워드 필요
                board_label = clean((soup.find("h2") or soup.find("h3") or soup.title).get_text(" ", strip=True) if (soup.find("h2") or soup.find("h3") or soup.title) else "")
                if not JOB_WORDS.search(title) and not BOARD_WORDS.search(board_label): continue
                page_recent += 1
                school = first_of(vals,["학교명","기관명"]) or school_from_title(title)
                raw_type = first_of(vals,["구분","직종","고용형태","분야"])
                raw_level = first_of(vals,["학교급별","학교급","급별"])
                apply_end = date_norm(first_of(vals,["접수마감일","마감일"]))
                region = first_of(vals,["지역"])
                if region not in regions: region = find_region(f"{row_text} {school} {title}", regions)
                if not region and len(regions)==1: region = regions[0]
                out.append({
                    "id":"goe-office-"+str(abs(hash(detail))),"province":"경기","school":school or office,"title":title,
                    "subject":first_of(vals,["과목","분야"]),"region":region,"regions":regions,"type":guess_type(raw_type+" "+title),
                    "schoolLevel":normalize_school_level(raw_level,school,title),"applyStart":"","applyEnd":apply_end,
                    "workStart":"","workEnd":"","registered":registered,"headcount":"",
                    "source":office,"checkedSources":[office],"sourceType":"교육지원청 개별 게시판","url":detail,"boardUrl":board,
                    "openMethod":"POST","openUrl":open_url,"openParams":{"job_seq":seq}
                })
        if page_candidates == 0: break
        if page_recent == 0 and page >= 2: break
        time.sleep(.04)
    return out, {"rawRows":raw_rows,"explicitEmpty":explicit_empty,"pagesScanned":pages_scanned}


def scrape_gyeonggi_office(src):
    office = src["name"]
    print("GYEONGGI OFFICE", office)
    boards = discover_gyeonggi_boards(src)
    all_rows, meta = [], []
    for b in boards:
        rows, m = scrape_mircms_board(b, src)
        all_rows.extend(rows); m["url"] = b; meta.append(m)
    raw_total = sum(x["rawRows"] for x in meta)
    explicit_empty = any(x["explicitEmpty"] for x in meta)
    if not boards:
        state, ok, msg = "error", False, "실제 채용 게시판을 찾지 못함"
    elif raw_total > 0:
        state, ok, msg = "ok", True, f"게시판 {len(boards)}개 확인"
    elif explicit_empty:
        state, ok, msg = "empty", True, "게시판 확인됨 · 현재 게시물 0건"
    else:
        state, ok, msg = "warning", False, "게시판은 찾았으나 게시물 구조 확인 필요"
    return all_rows, {"name":office,"url":src["url"],"boards":boards,"count":len(all_rows),"rawRows":raw_total,"ok":ok,"state":state,"message":msg}


# -------------------- 서울 중앙 --------------------
def parse_seoul_card(card):
    vals = {}
    for lab in card.select("p.tag_title"):
        key = clean(lab.get_text(" ", strip=True)); nxt = lab.find_next_sibling()
        if nxt: vals[key] = clean(nxt.get_text(" ", strip=True))
    return vals


def scrape_seoul_central():
    base_url = SEOUL["central"]["url"]
    jobs, seen = [], set()
    for page in range(1, 60):
        r = get(base_url, params={"type":"term","q_currPage":page,"q_rowPerPage":"50","q_sortBy":"regDt","q_recClosed":"closed"})
        if not r: break
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("#srchDataDiv li.flex_cont")
        if not cards: break
        added = 0
        for li in cards:
            a = li.find("a", href=re.compile(r"BD_selectRecDetail\.do\?q_rcrtSn="))
            if not a: continue
            m = re.search(r"q_rcrtSn=(\d+)", a.get("href", ""))
            if not m: continue
            rid=m.group(1)
            if rid in seen: continue
            seen.add(rid); added += 1
            s_title=clean(li.select_one(".s_title").get_text(" ",strip=True) if li.select_one(".s_title") else "")
            school=clean(s_title.split("|")[0]) if s_title else ""
            registered=date_norm(s_title)
            title=clean(li.select_one(".list_title").get_text(" ",strip=True) if li.select_one(".list_title") else "")
            vals=parse_seoul_card(li)
            subject=vals.get("과목(담당업무)","")
            region=find_region(vals.get("모집정보","")+" "+title+" "+school,SEOUL_REGIONS)
            apply_start,apply_end=split_period(vals.get("접수기간","")); work_start,work_end=split_period(vals.get("채용기간",""))
            jobs.append({
                "id":"sen-"+rid,"province":"서울","school":school,"title":title,"subject":subject,"region":region,
                "regions":[region] if region else [],"type":guess_type(vals.get("직무분야","")+" "+title+" "+subject),
                "schoolLevel":normalize_school_level("",school,title),"applyStart":apply_start,"applyEnd":apply_end,
                "workStart":work_start,"workEnd":work_end,"registered":registered,
                "headcount":re.sub(r"[^0-9명]+","",vals.get("채용인원","")),"source":SEOUL["central"]["name"],
                "checkedSources":[SEOUL["central"]["name"]],"sourceType":"통합게시판","url":urljoin(r.url,a.get("href",""))
            })
        if added == 0: break
        time.sleep(.05)
    return jobs


# -------------------- 서울 11개 지원청 --------------------
def seoul_seq_from_row(tr):
    html = str(tr)
    for pat in [r"fncDetailView\(['\"]?(\d+)", r"job_seq[=,'\"() ]+(\d+)", r"JOV11\.do[^\d]+(\d+)"]:
        m = re.search(pat, html, re.I)
        if m: return m.group(1)
    return ""


def scrape_seoul_office(src):
    office, board, regions = src["name"], src["boardUrl"], src.get("regions",[])
    print("SEOUL OFFICE", office)
    out, seen = [], set(); raw_rows=0; explicit_empty=False; got_table=False
    for page in range(1, 10):
        r = post(board,{"pageIndex":str(page),"searchPartPosition":"","searchCondition":"lesson","searchKeyword":""})
        if not r: r = get(board, params={"pageIndex":page})
        if not r: break
        soup=BeautifulSoup(r.text,"html.parser"); txt=clean(soup.get_text(" ",strip=True))
        if re.search(r"총\s*0\s*건|전체\s*0\s*건|등록된\s*게시물이\s*없",txt): explicit_empty=True
        page_raw=0; page_recent=0
        for table in soup.find_all("table"):
            headers=table_headers(table)
            if not headers: continue
            if not any("마감" in h or "직종" in h or "학교" in h for h in headers): continue
            got_table=True
            for tr in table.find_all("tr"):
                tds=tr.find_all("td")
                if not tds: continue
                seq=seoul_seq_from_row(tr)
                if not seq or seq in seen: continue
                seen.add(seq); raw_rows+=1; page_raw+=1
                vals={}
                for i,td in enumerate(tds):
                    if i<len(headers) and headers[i]: vals[headers[i]]=clean(td.get_text(" ",strip=True))
                anchors=[a for a in tr.find_all("a") if clean(a.get_text(" ",strip=True))]
                title=clean(max((a.get_text(" ",strip=True) for a in anchors),key=len,default=""))
                if not title: title=first_of(vals,["제목","공고명"])
                if len(title)<3 or EXCLUDE_WORDS.search(title): continue
                registered=date_norm(first_of(vals,["등록일","작성일"]))
                if not registered:
                    ds=all_dates(clean(tr.get_text(" ",strip=True))); registered=ds[-1] if ds else ""
                if registered and not recent_enough(registered,90): continue
                page_recent+=1
                school=first_of(vals,["학교명","기관명"]) or school_from_title(title)
                raw_level=first_of(vals,["학교급별","학교급"]); raw_type=first_of(vals,["직종","고용형태"])
                apply_end=date_norm(first_of(vals,["마감일","접수마감일"])); region=first_of(vals,["지역"])
                if region not in SEOUL_REGIONS: region=find_region(f"{region} {title} {school}",regions)
                if not region and len(regions)==1: region=regions[0]
                open_url=urljoin(board,"/FUS/JO/JOV11.do")
                detail=f"{open_url}?job_seq={seq}"
                out.append({
                    "id":f"sen-office-{urlparse(board).hostname}-{seq}","province":"서울","school":school or office,"title":title,
                    "subject":first_of(vals,["분야(과목)","분야","과목"]),"region":region,"regions":regions,
                    "type":guess_type(raw_type+" "+title),"schoolLevel":normalize_school_level(raw_level,school,title),
                    "applyStart":registered,"applyEnd":apply_end,"workStart":"","workEnd":"","registered":registered,"headcount":"",
                    "source":office,"checkedSources":[office],"sourceType":"교육지원청 개별 게시판","url":detail,"boardUrl":board
                })
        if page_raw==0: break
        if page_recent==0 and page>=2: break
        time.sleep(.04)
    if raw_rows>0:
        state,ok,msg="ok",True,"구인 게시판 확인"
    elif explicit_empty:
        state,ok,msg="empty",True,"구인 게시판 확인됨 · 현재 게시물 0건"
    elif got_table:
        state,ok,msg="warning",False,"게시판 표는 확인했으나 공고 행 분석 필요"
    else:
        state,ok,msg="error",False,"구인 게시판 구조를 읽지 못함"
    return out,{"name":office,"url":board,"boards":[board],"count":len(out),"rawRows":raw_rows,"ok":ok,"state":state,"message":msg}


def dedupe_merge(jobs):
    jobs=sorted(jobs,key=lambda j:0 if j.get("sourceType")=="통합게시판" else 1)
    by_key={}; out=[]
    for j in jobs:
        titlekey=norm(j.get("title","")); schoolkey=norm(j.get("school","")); province=j.get("province","")
        if not titlekey: continue
        key=province+"|"+titlekey if len(titlekey)>=14 else province+"|"+titlekey+"|"+schoolkey
        if key not in by_key:
            j["checkedSources"]=list(dict.fromkeys(j.get("checkedSources") or [j.get("source","")]))
            by_key[key]=j; out.append(j); continue
        old=by_key[key]
        sources=list(dict.fromkeys((old.get("checkedSources") or [old.get("source","")])+(j.get("checkedSources") or [j.get("source","")])))
        old["checkedSources"]=[x for x in sources if x]
        if len(old["checkedSources"])>1:
            old["source"]=" + ".join(old["checkedSources"][:3])+(f" 외 {len(old['checkedSources'])-3}곳" if len(old["checkedSources"])>3 else "")
        # 중앙 데이터에 없던 지역/학교/마감일은 지원청 데이터로 보완
        for field in ("region","school","applyEnd","subject"):
            if not old.get(field) and j.get(field): old[field]=j[field]
        old["regions"]=list(dict.fromkeys((old.get("regions") or [])+(j.get("regions") or [])))
    return out


def main():
    all_jobs=[]; errors=[]
    status={"gyeonggi":{"supportOffices":[]},"seoul":{"supportOffices":[]}}

    try:
        rows=scrape_gyeonggi_central(); all_jobs.extend(rows)
        status["gyeonggi"]["central"]={"name":GYEONGGI["central"]["name"],"url":GYEONGGI["central"]["url"],"count":len(rows),"ok":len(rows)>0,"state":"ok" if rows else "error"}
    except Exception as e:
        errors.append("경기도교육청:"+repr(e)); status["gyeonggi"]["central"]={"name":GYEONGGI["central"]["name"],"url":GYEONGGI["central"]["url"],"count":0,"ok":False,"state":"error"}

    for src in GYEONGGI["supportOffices"]:
        try:
            rows,st=scrape_gyeonggi_office(src); all_jobs.extend(rows); status["gyeonggi"]["supportOffices"].append(st)
            if not st["ok"]: errors.append(src["name"]+":"+st["message"])
        except Exception as e:
            errors.append(src["name"]+":"+repr(e)); status["gyeonggi"]["supportOffices"].append({"name":src["name"],"url":src["url"],"count":0,"rawRows":0,"ok":False,"state":"error","message":"수집 예외 발생"})

    try:
        rows=scrape_seoul_central(); all_jobs.extend(rows)
        status["seoul"]["central"]={"name":SEOUL["central"]["name"],"url":SEOUL["central"]["url"],"count":len(rows),"ok":len(rows)>0,"state":"ok" if rows else "error"}
    except Exception as e:
        errors.append("서울교육일자리포털:"+repr(e)); status["seoul"]["central"]={"name":SEOUL["central"]["name"],"url":SEOUL["central"]["url"],"count":0,"ok":False,"state":"error"}

    for src in SEOUL["supportOffices"]:
        try:
            rows,st=scrape_seoul_office(src); all_jobs.extend(rows); status["seoul"]["supportOffices"].append(st)
            if not st["ok"]: errors.append(src["name"]+":"+st["message"])
        except Exception as e:
            errors.append(src["name"]+":"+repr(e)); status["seoul"]["supportOffices"].append({"name":src["name"],"url":src["boardUrl"],"count":0,"rawRows":0,"ok":False,"state":"error","message":"수집 예외 발생"})

    CACHE_FILE.write_text(json.dumps(CACHE,ensure_ascii=False,indent=2),encoding="utf-8")
    all_jobs=dedupe_merge(all_jobs)
    all_jobs.sort(key=lambda j:(j.get("registered",""),j.get("applyEnd","")),reverse=True)
    payload={
        "updatedAt":NOW.strftime("%Y-%m-%d %H:%M KST"),
        "officialSourceCount":2+len(GYEONGGI["supportOffices"])+len(SEOUL["supportOffices"]),
        "sources":status,"errors":errors[:100],"verification":{"state":"pending","message":"2차 검증 전"},"jobs":all_jobs
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print("TOTAL",len(all_jobs),"sources",payload["officialSourceCount"],"issues",len(errors),"cached boards",sum(len(v) for v in CACHE.values()))

if __name__=="__main__":
    main()
