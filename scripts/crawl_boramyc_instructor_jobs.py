#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse, urlencode

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
LIST = "https://www.boramyc.or.kr/sub06/sub01.php"
HEADERS = {"User-Agent": "gg-edujob-source-audit/1.0 (+https://github.com/puzzlex5/gg-edujob)"}
MAX_PAGES = 8
DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")
PERIOD_RE = re.compile(
    r"접수기간\s*:\s*(20\d{2})\D+(\d{1,2})\D+(\d{1,2})"
    r"[^0-9]{0,50}(?:(20\d{2})\D+)?(\d{1,2})\D+(\d{1,2})",
    re.I,
)
DEADLINE_RE = re.compile(
    r"(?:접수\s*마감|마감일|접수\s*기한|지원\s*기한)\s*:?\s*"
    r"(20\d{2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2})",
    re.I,
)
INSTRUCTOR_RE = re.compile(r"(?:강사\s*(?:채용|모집)|(?:채용|모집)[^\n]{0,30}강사)", re.I)
RESULT_RE = re.compile(r"합격|결과|서류\s*(?:전형\s*)?결과|면접\s*(?:안내|대상)", re.I)


def norm(s): return re.sub(r"\s+", " ", str(s or "")).strip()

def iso_date(text):
    m = DATE_RE.search(str(text or ""))
    if not m: return ""
    try: return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
    except ValueError: return ""

def parse_apply_end(text):
    raw = str(text or "")
    m = PERIOD_RE.search(raw)
    if m:
        start_year, start_month = int(m.group(1)), int(m.group(2))
        explicit_end_year = int(m.group(4)) if m.group(4) else None
        end_month, end_day = int(m.group(5)), int(m.group(6))
        end_year = explicit_end_year if explicit_end_year is not None else start_year + (1 if end_month < start_month else 0)
        try: return datetime(end_year, end_month, end_day).date().isoformat()
        except ValueError: return ""
    m = DEADLINE_RE.search(raw)
    return iso_date(m.group(1)) if m else ""

def parser_regression():
    cases = {
        "접수기간 : 2026. 8. 26.(수) ~ 9. 10.(목)": "2026-09-10",
        "접수기간 : 2025. 11. 29. ~ 12. 16.": "2025-12-16",
        "접수기간 : 2025. 12. 20. ~ 1. 5.": "2026-01-05",
        "접수기간 : 2025. 12. 20. ~ 2026. 1. 5.": "2026-01-05",
        "접수 마감: 2026-09-18": "2026-09-18",
    }
    for sample, expected in cases.items():
        actual = parse_apply_end(sample)
        if actual != expected: raise RuntimeError(f"deadline parser regression: {sample!r}: {actual!r} != {expected!r}")

def exact_url(idx):
    return f"{LIST}?{urlencode({'code':'notice','idx':str(idx),'ptype':'view'})}"

def get(session, url, **kwargs):
    last = None
    for attempt in range(3):
        try:
            r = session.get(url, headers=HEADERS, timeout=30, allow_redirects=True, **kwargs)
            last = r
            if r.status_code < 500:
                r.raise_for_status(); return r
        except requests.RequestException:
            if attempt == 2: raise
    assert last is not None
    last.raise_for_status()

def list_rows(session, page):
    r = get(session, LIST, params={"page": page, "code": "notice"})
    soup = BeautifulSoup(r.text, "html.parser")
    found = {}
    for a in soup.find_all("a", href=True):
        title = norm(a.get_text(" ", strip=True))
        href = urljoin(r.url, a.get("href") or "")
        q = parse_qs(urlparse(href).query)
        idx = str((q.get("idx") or [""])[0]); ptype = str((q.get("ptype") or [""])[0])
        if idx.isdigit() and ptype == "view" and title:
            found[idx] = {"idx": idx, "title": title}
    return list(found.values())

def detail(session, meta):
    idx = meta["idx"]; url = exact_url(idx); r = get(session, url)
    q = parse_qs(urlparse(r.url).query); final_idx = str((q.get("idx") or [idx])[0])
    text = norm(BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True))
    if final_idx != idx or meta["title"] not in text or "보라매청소년센터" not in text:
        raise RuntimeError(f"detail identity mismatch idx={idx} final={final_idx}")
    registered = ""
    m = re.search(r"작성일\s*:\s*(20\d{2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2})", text)
    if m: registered = iso_date(m.group(1))
    apply_end = parse_apply_end(text)
    terms = []
    for term in ("당구","탁구","포켓볼","체육","생활체육","음악","미술","연극","무용","국악","오케스트라","합창","방과후","늘봄"):
        if term in text and term not in terms: terms.append(term)
    return {
        "sourceIdentity": f"boramyc:{idx}", "source": "시립보라매청소년센터",
        "sourceSurface": "youth-instructor", "sourceSurfaceLabel": "보라매청소년센터 강사채용",
        "province": "서울", "region": "동작구", "regions": ["동작구"], "provinces": ["서울"],
        "location": "서울 동작구", "title": meta["title"], "subject": " · ".join(terms),
        "registered": registered, "applyEnd": apply_end, "originalUrl": url, "url": url,
        "detailLinkVerified": True,
    }

def main():
    parser_regression()
    session = requests.Session(); errors = []; all_rows = {}; page_sets = []
    for page in range(1, MAX_PAGES + 1):
        rows = list_rows(session, page); ids = {x["idx"] for x in rows}
        if not ids:
            if page == 1: errors.append("first list page had no stable detail IDs")
            break
        page_sets.append(ids)
        for row in rows: all_rows[row["idx"]] = row
    traversal_complete = bool(page_sets) and len(set().union(*page_sets)) > len(page_sets[0])
    candidates = [r for r in all_rows.values() if INSTRUCTOR_RE.search(r["title"]) and not RESULT_RE.search(r["title"])]
    jobs = []
    for row in candidates:
        try: jobs.append(detail(session, row))
        except Exception as exc: errors.append(f"{row['idx']}: {exc}")
    target = next((j for j in jobs if j["sourceIdentity"] == "boramyc:25441"), None)
    if not target: errors.append("current live instructor posting boramyc:25441 missing")
    elif target.get("applyEnd") != "2026-09-10" or "당구" not in target.get("subject", ""):
        errors.append("boramyc:25441 content/deadline mismatch")
    stale_year_errors = [
        j["sourceIdentity"] for j in jobs
        if str(j.get("registered") or "").startswith("2025-") and str(j.get("applyEnd") or "").startswith("2026-")
        and not (str(j.get("registered") or "").startswith("2025-12-") and str(j.get("applyEnd") or "").startswith("2026-01-"))
    ]
    if stale_year_errors: errors.append("implausible yearless deadline rollover: " + ",".join(stale_year_errors))
    healthy = traversal_complete and bool(jobs) and not errors
    report = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"), "source": "시립보라매청소년센터",
        "publicationEnabled": False, "healthy": healthy, "traversalComplete": traversal_complete,
        "pagesScanned": len(page_sets), "sourceIdCount": len(all_rows), "candidateIdCount": len(candidates),
        "jobCount": len(jobs), "missingAfterCount": 0 if healthy else 1, "detailErrorCount": len(errors), "errors": errors,
    }
    Path("boramyc_reconciliation_report.candidate.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("boramyc_jobs.candidate.json").write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    if not healthy: raise SystemExit("Boramae collector fail-closed: " + "; ".join(errors or ["unhealthy traversal"]))
    canonical = dict(report); canonical["publicationEnabled"] = True
    Path("boramyc_jobs.json").write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("boramyc_reconciliation_report.json").write_text(json.dumps(canonical, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(canonical, ensure_ascii=False, indent=2))

if __name__ == "__main__": main()
