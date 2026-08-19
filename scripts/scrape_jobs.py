#!/usr/bin/env python3
import json, re, time, unicodedata
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
UA = "Mozilla/5.0 (compatible; gg-edujob/1.0; public recruitment aggregator)"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6"})

CENTRAL_URL = SOURCES["central"]["url"]
CENTRAL_BASE = "https://www.goe.go.kr"
CATEGORIES = {
    "A": ("기간제/사립교원", "기간제교원"),
    "B": ("초중등시간강사", "시간강사/강사"),
    "C": ("교육공무직원", "교육공무직"),
    "D": ("사립학교 교원정규채용", "정규채용"),
    "E": ("사립학교 사무직정규채용", "정규채용"),
    "F": ("자원봉사자", "자원봉사"),
}
REGIONS = ["수원시","성남시","용인시","고양시","화성시","안산시","평택시","파주시","김포시","남양주시","부천시","안양시","시흥시","광명시","광주시","군포시","이천시","오산시","안성시","의왕시","하남시","여주시","양평군","과천시","의정부시","양주시","구리시","포천시","동두천시","가평군","연천군"]
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
    if "유치원" in t: return "유치원"
    if "초등학교" in t or re.search(r"[가-힣]+초\b", t): return "초등학교"
    if "중학교" in t or re.search(r"[가-힣]+중\b", t): return "중학교"
    if "고등학교" in t or re.search(r"[가-힣]+고\b", t): return "고등학교"
    if "특수학교" in t: return "특수학교"
    if "교육지원청" in t: return "교육지원청"
    return "기타"


def guess_type(title):
    t = title or ""
    if "자원봉사" in t or "봉사자" in t: return "자원봉사"
    if "교육공무직" in t or "조리실무" in t or "보육전담" in t: return "교육공무직"
    if "시간강사" in t or "강사" in t: return "시간강사/강사"
    if "정규" in t or "공개경쟁" in t and ("사무직" in t or "교원" in t): return "정규채용"
    if "기간제" in t or "교원" in t or "교사" in t: return "기간제교원"
    return "기타"


def get(url, **kwargs):
    try:
        r = S.get(url, timeout=18, allow_redirects=True, **kwargs)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception as e:
        print("GET FAIL", url, type(e).__name__, str(e)[:120])
        return None


def post(url, data):
    try:
        r = S.post(url, data=data, timeout=20, allow_redirects=True)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception as e:
        print("POST FAIL", url, type(e).__name__, str(e)[:120])
        return None


def parse_labelled(container):
    vals = {}
    for p in container.select(".cont_btm p"):
        lab = p.select_one(".btm_tit")
        if not lab:
            continue
        key = clean(lab.get_text(" ", strip=True))
        txt = clean(p.get_text(" ", strip=True).replace(key, "", 1))
        vals[key] = txt
    return vals


def scrape_central():
    jobs = []
    seen_ids = set()
    for code, (cat_name, ui_type) in CATEGORIES.items():
        print("CENTRAL", code, cat_name)
        for page in range(1, 31):
            data = {
                "mi": "10502", "pbancSn": "", "currPage": str(page),
                "srchEcptDl": "Y", "srchTodayPb": "", "srchLgnNm": "",
                "srchOcptNm": cat_name, "srchOcptCd": code,
                "pageIndex": "50", "orderbyType": "reg", "searchType": "", "searchValue": "",
                "btchDlYn": "", "cndNo": "", "srchSchlSe": ""
            }
            r = post(CENTRAL_URL, data)
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
                seen_ids.add(pid); added += 1
                top = [clean(x.get_text(" ", strip=True)) for x in li.select(".cont_top span")]
                school = top[0] if top else ""
                registered = ""
                for x in top:
                    if "등록일" in x:
                        registered = date_norm(x)
                        break
                title_el = li.select_one(".cont_tit")
                title = clean(title_el.get_text(" ", strip=True) if title_el else "")
                title = clean(re.sub(r"^(마감임박|오늘등록|NEW)\s*", "", title))
                vals = parse_labelled(li)
                apply_start, apply_end = split_period(vals.get("접수기간", ""))
                work_start, work_end = split_period(vals.get("채용기간", ""))
                subject = vals.get("직무분야", "")
                headcount = re.sub(r"[^0-9명]+", "", vals.get("채용인원", ""))
                body_text = clean(li.get_text(" ", strip=True))
                region = next((x for x in REGIONS if x in body_text), "")
                jobs.append({
                    "id": "goe-" + pid,
                    "school": school,
                    "title": title,
                    "subject": subject,
                    "region": region,
                    "regions": [region] if region else [],
                    "type": ui_type,
                    "schoolLevel": guess_school_level(school + " " + title),
                    "applyStart": apply_start, "applyEnd": apply_end,
                    "workStart": work_start, "workEnd": work_end,
                    "registered": registered, "headcount": headcount,
                    "source": "경기도교육청 통합 구인구직",
                    "sourceType": "통합게시판",
                    "url": f"https://www.goe.go.kr/recruit/ad/func/pb/hnfpPbancView.do?mi=10502&pbancSn={pid}"
                })
            if added == 0 or len(rows) < 45:
                break
            time.sleep(0.12)
    return jobs


def same_site(base, url):
    b = urlparse(base); u = urlparse(url)
    return (u.hostname or "").replace("www.", "") == (b.hostname or "").replace("www.", "")


def candidate_boards(base_url):
    r = get(base_url)
    if not r: return []
    soup = BeautifulSoup(r.text, "html.parser")
    pages = [(r.url, soup)]
    # 일부 교육지원청은 전체 메뉴/사이트맵에 채용 게시판 링크가 더 잘 노출된다.
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
            context = clean((a.parent.get_text(" ", strip=True) if a.parent else txt))
            u = urljoin(page_url, href)
            if not same_site(base_url, u):
                continue
            if "selectNttList.do" not in u and "cntntsView.do" not in u:
                continue
            if JOB_WORDS.search(txt + " " + context) or re.search(r"구인|채용|기간제|공무직", u, re.I):
                found.append(u)
    # 순서를 유지한 중복 제거, 과도한 크롤링 방지
    return list(dict.fromkeys(found))[:14]


def row_date(node):
    text = clean(node.get_text(" ", strip=True))
    dates = [date_norm(m.group(0)) for m in DATE_RE.finditer(text)]
    return dates[-1] if dates else ""


def recent_enough(ds, days=60):
    if not ds: return True
    try:
        d = datetime.strptime(ds, "%Y/%m/%d").replace(tzinfo=KST)
        return d >= NOW - timedelta(days=days)
    except Exception:
        return True


def scrape_support_office(src):
    office = src["name"]; base = src["url"]; regions = src.get("regions", [])
    print("OFFICE", office)
    boards = candidate_boards(base)
    print(" boards", len(boards))
    out = []
    seen = set()
    for board in boards:
        r = get(board)
        if not r: continue
        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.find_all("a", href=True)
        for a in links:
            href = a.get("href", "")
            if "selectNttInfo.do" not in href:
                continue
            title = clean(a.get_text(" ", strip=True))
            if len(title) < 4 or not JOB_WORDS.search(title) or EXCLUDE_WORDS.search(title):
                continue
            url = urljoin(r.url, href)
            if url in seen: continue
            seen.add(url)
            parent = a.find_parent("tr") or a.find_parent("li") or a.parent
            registered = row_date(parent) if parent else ""
            if registered and not recent_enough(registered, 60):
                continue
            region = regions[0] if len(regions) == 1 else ""
            out.append({
                "id": "office-" + str(abs(hash(url))),
                "school": office,
                "title": title,
                "subject": "",
                "region": region,
                "regions": regions,
                "type": guess_type(title),
                "schoolLevel": guess_school_level(title + " " + office),
                "applyStart": "", "applyEnd": "",
                "workStart": "", "workEnd": "",
                "registered": registered, "headcount": "",
                "source": office,
                "sourceType": "교육지원청 개별 게시판",
                "url": url
            })
            if len(out) >= 120: break
        if len(out) >= 120: break
        time.sleep(0.08)
    return out


def dedupe(jobs):
    # 통합게시판을 우선하고, 같은 제목의 교육지원청 중복 게시물을 제거한다.
    jobs = sorted(jobs, key=lambda j: 0 if j.get("sourceType") == "통합게시판" else 1)
    out, seen = [], set()
    for j in jobs:
        titlekey = norm(j.get("title", ""))
        schoolkey = norm(j.get("school", ""))
        # 중앙-지원청 간에는 제목만 같아도 중복으로 본다. 너무 짧은 제목은 학교명까지 사용.
        key = titlekey if len(titlekey) >= 12 else titlekey + "|" + schoolkey
        if not key or key in seen: continue
        seen.add(key); out.append(j)
    return out


def main():
    all_jobs = []
    errors = []
    try:
        all_jobs.extend(scrape_central())
    except Exception as e:
        errors.append("중앙:" + repr(e))
    office_status = []
    for src in SOURCES["supportOffices"]:
        before = len(all_jobs)
        try:
            rows = scrape_support_office(src)
            all_jobs.extend(rows)
            office_status.append({"name": src["name"], "url": src["url"], "count": len(rows), "ok": True})
        except Exception as e:
            errors.append(src["name"] + ":" + repr(e))
            office_status.append({"name": src["name"], "url": src["url"], "count": 0, "ok": False})
    all_jobs = dedupe(all_jobs)
    all_jobs.sort(key=lambda j: (j.get("registered", ""), j.get("applyEnd", "")), reverse=True)
    payload = {
        "updatedAt": NOW.strftime("%Y-%m-%d %H:%M KST"),
        "supportOfficeCount": len(SOURCES["supportOffices"]),
        "sources": {"central": SOURCES["central"], "supportOffices": office_status},
        "errors": errors[:30],
        "jobs": all_jobs
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("TOTAL", len(all_jobs), "CENTRAL+OFFICES", len(SOURCES["supportOffices"]), "errors", len(errors))

if __name__ == "__main__":
    main()
