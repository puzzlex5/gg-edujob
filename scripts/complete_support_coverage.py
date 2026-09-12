#!/usr/bin/env python3
"""Second-pass support-office crawler focused on completeness, not merely parser success."""
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

from support_population_contract import is_support_population_job

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
MAX_PAGES = 500
UA = "Mozilla/5.0 (compatible; metro-edujob-completeness/1.0; public recruitment aggregator)"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6"})
RETRY_POLICY = Retry(total=3, connect=3, read=3, status=3, backoff_factor=0.8, status_forcelist=(408,429,500,502,503,504), allowed_methods=frozenset(("GET","POST")), respect_retry_after_header=True, raise_on_status=False)
S.mount("https://", HTTPAdapter(max_retries=RETRY_POLICY)); S.mount("http://", HTTPAdapter(max_retries=RETRY_POLICY))
DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")
JOB_WORDS = re.compile(r"채용|구인|모집|기간제|계약제|시간강사|강사|교사|교원|공무직|사무직|근로자|조리|돌봄|보육|봉사|튜터|안전지킴이|외부강사")
EXCLUDE_WORDS = re.compile(r"최종\s*합격|합격자|서류\s*심사|서류전형|면접\s*대상|선정\s*결과|채용\s*결과|전형\s*결과|합격\s*공고|인사발령")

def clean(s): return re.sub(r"\s+", " ", (s or "")).strip()
def date_norm(s):
    m=DATE_RE.search(s or "")
    return f"{int(m.group(1)):04d}/{int(m.group(2)):02d}/{int(m.group(3)):02d}" if m else ""
def all_dates(s): return [date_norm(m.group(0)) for m in DATE_RE.finditer(s or "")]
def recent(ds):
    if not ds: return True
    try: return datetime.strptime(ds,"%Y/%m/%d").replace(tzinfo=KST) >= NOW-timedelta(days=LOOKBACK_DAYS)
    except Exception: return True
def definitely_old(ds):
    if not ds: return False
    try: return datetime.strptime(ds,"%Y/%m/%d").replace(tzinfo=KST) < NOW-timedelta(days=LOOKBACK_DAYS)
    except Exception: return False
def get(url, **kwargs):
    try:
        r=S.get(url,timeout=20,allow_redirects=True,**kwargs); r.raise_for_status()
        if not r.encoding or r.encoding.lower()=="iso-8859-1": r.encoding=r.apparent_encoding or "utf-8"
        return r
    except Exception: return None
def post(url,data):
    try:
        r=S.post(url,data=data,timeout=22,allow_redirects=True); r.raise_for_status()
        if not r.encoding or r.encoding.lower()=="iso-8859-1": r.encoding=r.apparent_encoding or "utf-8"
        return r
    except Exception: return None
def with_query(url,**changes):
    p=urlparse(url); q=parse_qs(p.query,keep_blank_values=True)
    for k,v in changes.items(): q[k]=[str(v)]
    return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),p.fragment))
def headers(table):
    tr=table.find("tr"); return [clean(x.get_text(" ",strip=True)) for x in tr.find_all("th")] if tr else []
def first_of(vals,keys):
    for key in keys:
        for actual,value in vals.items():
            if key in actual and value: return value
    return ""
def school_from_title(title):
    m=re.match(r"\s*[\[(]([^\])]+)[\])]",title or "")
    if m: return clean(m.group(1))
    m=re.search(r"([가-힣A-Za-z0-9·]+(?:유치원|초등학교|중학교|고등학교|학교))",title or "")
    return clean(m.group(1)) if m else ""
def detail_from_anchor(board,a):
    href=a.get("href","") or ""; data_id=clean(str(a.get("data-id", ""))); raw=href+" "+(a.get("onclick","") or "")
    ntt=data_id if re.fullmatch(r"\d{4,12}",data_id) else ""
    if not ntt:
        m=re.search(r"nttSn\s*[=:,'\"() ]+\s*(\d{4,})",raw,re.I) or re.search(r"(?:nttView|selectNttInfo|goView)\D+(\d{4,})",raw,re.I)
        ntt=m.group(1) if m else ""
    if not ntt: return ""
    p=urlparse(board); q=parse_qs(p.query); bbs=(q.get("bbsId") or [""])[0]
    if not bbs: return ""
    params={"bbsId":bbs,"nttSn":ntt}
    for k in ("mi","clasHmpgId"):
        if q.get(k): params[k]=q[k][0]
    return urlunparse((p.scheme,p.netloc,p.path.replace("selectNttList.do","selectNttInfo.do"),"",urlencode(params),""))
def row_vals(tr,hs): return {hs[i]:clean(td.get_text(" ",strip=True)) for i,td in enumerate(tr.find_all("td")) if i<len(hs) and hs[i]}

# The remaining crawler traversal is unchanged; production output is finally normalized through
# the shared population contract immediately before it is merged into jobs.json.

def production_filter(rows):
    return [r for r in rows if is_support_population_job(r, as_of=NOW)]

