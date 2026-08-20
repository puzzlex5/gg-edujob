#!/usr/bin/env python3
"""Resolve exact individual posting links for support-office jobs.

Gyeonggi MirCMS list pages sometimes expose the internal nttSn only inside
JavaScript/data attributes. This pass scans the raw row HTML, reconstructs the
canonical selectNttInfo.do URL and writes the identifiers back to jobs.json.
Seoul support-office postings are normalized to the POST bridge fields used by
open-job.html.
"""
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SOURCES = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
JOBS_PATH = ROOT / "jobs.json"
REPORT_PATH = ROOT / "support_link_resolution.json"
S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; metro-edujob-link-resolver/2.0)",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
})

RETRY_POLICY = Retry(
    total=3, connect=3, read=3, status=3, backoff_factor=0.8,
    status_forcelist=(408, 429, 500, 502, 503, 504),
    allowed_methods=frozenset(("GET", "POST")),
    respect_retry_after_header=True, raise_on_status=False,
)
S.mount("https://", HTTPAdapter(max_retries=RETRY_POLICY))
S.mount("http://", HTTPAdapter(max_retries=RETRY_POLICY))


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def norm(s):
    return re.sub(r"[^0-9a-z가-힣]+", "", clean(s).lower())


def get(url):
    try:
        r = S.get(url, timeout=20, allow_redirects=True)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception:
        return None


def with_page(url, page):
    p = urlparse(url)
    q = parse_qs(p.query, keep_blank_values=True)
    q["currPage"] = [str(page)]
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q, doseq=True), p.fragment))


def board_parts(board):
    p = urlparse(board)
    q = parse_qs(p.query, keep_blank_values=True)
    return p, (q.get("bbsId") or [""])[0], (q.get("mi") or [""])[0], (q.get("clasHmpgId") or [""])[0]


def canonical_detail(board, ntt_sn):
    p, bbs, mi, clas = board_parts(board)
    if not bbs or not str(ntt_sn).isdigit():
        return ""
    params = {"bbsId": bbs, "nttSn": str(ntt_sn)}
    if mi:
        params["mi"] = mi
    if clas:
        params["clasHmpgId"] = clas
    path = p.path.replace("selectNttList.do", "selectNttInfo.do")
    return urlunparse((p.scheme, p.netloc, path, "", urlencode(params), ""))


def extract_ntt_sn(board, tr):
    # Direct canonical href is strongest.
    for a in tr.find_all("a"):
        href = a.get("href", "") or ""
        if "selectNttInfo.do" in href:
            u = urljoin(board, href)
            q = parse_qs(urlparse(u).query)
            sn = (q.get("nttSn") or [""])[0]
            if sn.isdigit():
                return sn, u, "direct-href"

    html = str(tr)
    strong_patterns = [
        r"(?:data[-_]?ntt[-_]?sn|nttSn|ntt_sn)\s*[=:]['\" ]*(\d{5,10})",
        r"(?:selectNttInfo|fncNttView|fnNttView|nttView|goView)\s*\([^)]*?['\"]?(\d{5,10})",
        r"(?:selectNttInfo|fncNttView|fnNttView|nttView|goView)[^0-9]{0,140}(\d{5,10})",
        r"['\"]nttSn['\"]\s*[:,=]\s*['\"]?(\d{5,10})",
    ]
    for pat in strong_patterns:
        m = re.search(pat, html, re.I)
        if m:
            sn = m.group(1)
            return sn, canonical_detail(board, sn), "strong-pattern"

    # MirCMS internal nttSn values are normally 6-8 digits. Display sequence,
    # dates and view counts are much shorter, so this recovers skins that hide
    # the ID in an unnamed data argument.
    candidates = []
    for tag in tr.find_all(True):
        for key, value in tag.attrs.items():
            vals = value if isinstance(value, list) else [value]
            for raw in vals:
                for m in re.finditer(r"(?<!\d)(\d{6,10})(?!\d)", str(raw)):
                    n = m.group(1)
                    if n.startswith("2026") or n.startswith("2025"):
                        continue
                    candidates.append((n, f"attr:{key}"))
    if not candidates:
        for m in re.finditer(r"(?<!\d)(\d{6,10})(?!\d)", html):
            n = m.group(1)
            if n.startswith("2026") or n.startswith("2025"):
                continue
            candidates.append((n, "row-number"))

    seen = set()
    for sn, how in candidates:
        if sn in seen:
            continue
        seen.add(sn)
        return sn, canonical_detail(board, sn), how
    return "", "", "unresolved"


def table_headers(table):
    tr = table.find("tr")
    return [clean(x.get_text(" ", strip=True)) for x in tr.find_all("th")] if tr else []


def first_of(vals, needles):
    for needle in needles:
        for key, value in vals.items():
            if needle in key and value:
                return value
    return ""


def title_from_row(tr, headers):
    tds = tr.find_all("td")
    vals = {headers[i]: clean(td.get_text(" ", strip=True)) for i, td in enumerate(tds) if i < len(headers) and headers[i]}
    title = first_of(vals, ["제목", "공고명"])
    if title:
        return clean(title.replace("새로운 글", ""))
    anchors = [clean(a.get_text(" ", strip=True)) for a in tr.find_all("a")]
    anchors = [x for x in anchors if len(x) >= 3]
    return max(anchors, key=len, default="")


def scan_gyeonggi_office(src):
    office = src["name"]
    resolved = {}
    board_stats = []
    for board in src.get("boardUrls", []):
        found = 0
        methods = {}
        for page in range(1, 9):
            r = get(with_page(board, page))
            if not r:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            page_rows = 0
            for table in soup.find_all("table"):
                headers = table_headers(table)
                if not headers:
                    continue
                for tr in table.find_all("tr"):
                    if not tr.find_all("td"):
                        continue
                    title = title_from_row(tr, headers)
                    if len(title) < 3:
                        continue
                    sn, detail, how = extract_ntt_sn(r.url, tr)
                    if not sn or not detail:
                        continue
                    page_rows += 1
                    found += 1
                    methods[how] = methods.get(how, 0) + 1
                    p, bbs, mi, _ = board_parts(r.url)
                    resolved[(office, norm(title))] = {
                        "url": detail,
                        "nttSn": sn,
                        "bbsId": bbs,
                        "mi": mi,
                        "boardUrl": board,
                        "method": how,
                    }
            if page_rows == 0:
                break
        board_stats.append({"board": board, "resolvedRows": found, "methods": methods})
    return resolved, board_stats


def normalize_seoul_job(job):
    raw = job.get("url", "") or ""
    seq = ""
    if job.get("openParams", {}).get("job_seq"):
        seq = str(job["openParams"]["job_seq"])
    if not seq and raw:
        try:
            seq = (parse_qs(urlparse(raw).query).get("job_seq") or [""])[0]
        except Exception:
            seq = ""
    if not seq.isdigit():
        return False
    base = job.get("openUrl", "") or ""
    if not base:
        board = job.get("boardUrl", "") or raw
        p = urlparse(board)
        base = urlunparse((p.scheme, p.netloc, "/FUS/JO/JOV11.do", "", "", ""))
    if not base.startswith("https://") or not urlparse(base).hostname or not urlparse(base).hostname.endswith(".sen.go.kr"):
        return False
    job["openMethod"] = "POST"
    job["openUrl"] = base
    job["openParams"] = {"job_seq": seq}
    job["url"] = f"{base}?job_seq={seq}"
    job["detailLinkResolved"] = True
    return True


def main():
    payload = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    all_map = {}
    office_reports = []
    for src in SOURCES["gyeonggi"]["supportOffices"]:
        mapping, stats = scan_gyeonggi_office(src)
        all_map.update(mapping)
        office_reports.append({"name": src["name"], "resolvedTitles": len(mapping), "boards": stats})
        print("SCAN", src["name"], "resolved", len(mapping))

    gyeonggi_support = 0
    gyeonggi_resolved = 0
    seoul_support = 0
    seoul_resolved = 0
    unresolved_examples = []

    for job in payload.get("jobs", []):
        if job.get("sourceType") != "교육지원청 개별 게시판":
            continue
        province = job.get("province", "")
        if province == "경기":
            gyeonggi_support += 1
            source_names = job.get("checkedSources") or [job.get("source", "")]
            hit = None
            for office in source_names:
                hit = all_map.get((office, norm(job.get("title", ""))))
                if hit:
                    break
            if not hit:
                hit = all_map.get((job.get("source", ""), norm(job.get("title", ""))))
            if hit:
                job.update({
                    "url": hit["url"],
                    "boardUrl": hit["boardUrl"],
                    "detailLinkResolved": True,
                    "nttSn": hit["nttSn"],
                    "bbsId": hit["bbsId"],
                    "mi": hit["mi"],
                    "detailResolutionMethod": hit["method"],
                })
                gyeonggi_resolved += 1
            elif job.get("url") and "selectNttInfo.do" in job.get("url", ""):
                q = parse_qs(urlparse(job["url"]).query)
                sn = (q.get("nttSn") or [""])[0]
                bbs = (q.get("bbsId") or [""])[0]
                if sn.isdigit() and bbs:
                    job["detailLinkResolved"] = True
                    job["nttSn"] = sn
                    job["bbsId"] = bbs
                    job["mi"] = (q.get("mi") or [""])[0]
                    gyeonggi_resolved += 1
            else:
                job["detailLinkResolved"] = False
                if len(unresolved_examples) < 30:
                    unresolved_examples.append({"source": job.get("source"), "title": job.get("title")})
        elif province == "서울":
            seoul_support += 1
            if normalize_seoul_job(job):
                seoul_resolved += 1

    report = {
        "gyeonggi": {
            "supportJobs": gyeonggi_support,
            "exactLinks": gyeonggi_resolved,
            "unresolved": gyeonggi_support - gyeonggi_resolved,
            "offices": office_reports,
        },
        "seoul": {
            "supportJobs": seoul_support,
            "exactLinks": seoul_resolved,
            "unresolved": seoul_support - seoul_resolved,
        },
        "unresolvedExamples": unresolved_examples,
    }
    payload["supportLinkResolution"] = {
        "gyeonggiExact": gyeonggi_resolved,
        "gyeonggiTotal": gyeonggi_support,
        "seoulExact": seoul_resolved,
        "seoulTotal": seoul_support,
    }
    JOBS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("LINK RESOLUTION", payload["supportLinkResolution"])


if __name__ == "__main__":
    main()
