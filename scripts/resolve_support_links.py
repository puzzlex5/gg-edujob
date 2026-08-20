#!/usr/bin/env python3
"""Resolve exact individual posting links for support-office jobs.

Gyeonggi MirCMS list pages sometimes expose the internal nttSn only inside
JavaScript/data attributes. This pass preserves already-canonical detail URLs and
only scans official boards when a legacy/current row is still unresolved. Those
fallback scans are target-aware: they continue until all needed titles are found,
the board naturally ends/repeats, or a generous safety cap is reached.
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
MAX_FALLBACK_PAGES = 60
S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; metro-edujob-link-resolver/3.0)",
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


def scan_gyeonggi_office(src, target_titles):
    """Scan only as far as needed for unresolved titles.

    Exact canonical URLs are preserved before this function is called, so a normal
    refresh usually performs no fallback scans at all. If fallback is necessary,
    page signatures prevent infinite/repeated pagination and the scan never assumes
    that 8 pages (80 rows) means the board is complete.
    """
    office = src["name"]
    targets = set(target_titles or ())
    resolved = {}
    board_stats = []
    if not targets:
        return resolved, board_stats

    remaining = set(targets)
    for board in src.get("boardUrls", []):
        if not remaining:
            break
        found = 0
        methods = {}
        seen_page_sigs = set()
        natural_end = False
        repeated = False
        access_error = False
        cap_hit = False
        pages_scanned = 0

        for page in range(1, MAX_FALLBACK_PAGES + 1):
            r = get(with_page(board, page))
            if not r:
                access_error = True
                break
            soup = BeautifulSoup(r.text, "html.parser")
            page_rows = []
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
                    page_rows.append((sn, title, detail, how, r.url))

            pages_scanned = page
            if not page_rows:
                natural_end = True
                break

            sig = tuple(x[0] for x in page_rows)
            if sig in seen_page_sigs:
                repeated = True
                break
            seen_page_sigs.add(sig)

            for sn, title, detail, how, resolved_board_url in page_rows:
                nt = norm(title)
                if nt not in remaining:
                    continue
                found += 1
                methods[how] = methods.get(how, 0) + 1
                _, bbs, mi, _ = board_parts(resolved_board_url)
                resolved[(office, nt)] = {
                    "url": detail,
                    "nttSn": sn,
                    "bbsId": bbs,
                    "mi": mi,
                    "boardUrl": board,
                    "method": how,
                }
                remaining.discard(nt)
            if not remaining:
                break
        else:
            cap_hit = True

        board_stats.append({
            "board": board,
            "resolvedTargets": found,
            "pagesScanned": pages_scanned,
            "naturalEnd": natural_end,
            "paginationRepeated": repeated,
            "accessError": access_error,
            "capHit": cap_hit,
            "remainingTargets": len(remaining),
            "methods": methods,
        })
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


def exact_gyeonggi_url(job):
    raw = job.get("url", "") or ""
    if "selectNttInfo.do" not in raw:
        return False
    q = parse_qs(urlparse(raw).query)
    sn = (q.get("nttSn") or [""])[0]
    bbs = (q.get("bbsId") or [""])[0]
    if not (sn.isdigit() and bbs):
        return False
    job["detailLinkResolved"] = True
    job["nttSn"] = sn
    job["bbsId"] = bbs
    job["mi"] = (q.get("mi") or [""])[0]
    return True


def main():
    payload = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    jobs = payload.get("jobs", [])

    gyeonggi_support = 0
    gyeonggi_resolved = 0
    seoul_support = 0
    seoul_resolved = 0
    unresolved_examples = []
    unresolved_gg = []
    targets_by_office = {}

    # First preserve canonical identifiers. Only genuinely unresolved Gyeonggi rows
    # become fallback scan targets, avoiding a full board rescan every two hours.
    for job in jobs:
        if job.get("sourceType") != "교육지원청 개별 게시판":
            continue
        province = job.get("province", "")
        if province == "경기":
            gyeonggi_support += 1
            if exact_gyeonggi_url(job):
                gyeonggi_resolved += 1
                continue
            unresolved_gg.append(job)
            title_key = norm(job.get("title", ""))
            if title_key:
                offices = job.get("checkedSources") or [job.get("source", "")]
                for office in offices:
                    if office:
                        targets_by_office.setdefault(office, set()).add(title_key)
        elif province == "서울":
            seoul_support += 1
            if normalize_seoul_job(job):
                seoul_resolved += 1

    all_map = {}
    office_reports = []
    for src in SOURCES["gyeonggi"]["supportOffices"]:
        targets = targets_by_office.get(src["name"], set())
        mapping, stats = scan_gyeonggi_office(src, targets)
        all_map.update(mapping)
        office_reports.append({
            "name": src["name"],
            "targetTitles": len(targets),
            "resolvedTargets": len(mapping),
            "boards": stats,
        })
        if targets:
            print("SCAN", src["name"], "targets", len(targets), "resolved", len(mapping))

    for job in unresolved_gg:
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
        else:
            job["detailLinkResolved"] = False
            if len(unresolved_examples) < 30:
                unresolved_examples.append({"source": job.get("source"), "title": job.get("title")})

    report = {
        "gyeonggi": {
            "supportJobs": gyeonggi_support,
            "exactLinks": gyeonggi_resolved,
            "unresolved": gyeonggi_support - gyeonggi_resolved,
            "fallbackTargetTitles": sum(len(v) for v in targets_by_office.values()),
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
