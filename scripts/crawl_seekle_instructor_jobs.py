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
LIST = "https://www.seekle.or.kr/sub07/sub01.php"
# Keep the same benign audit UA/request shape that was content-level verified in PR #47.
HEADERS = {"User-Agent": "gg-edujob-source-audit/1.0 (+https://github.com/puzzlex5/gg-edujob)"}
MAX_PAGES = 8
DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")
INSTRUCTOR_RE = re.compile(r"강사\s*(?:채용|모집)|신규\s*강사", re.I)
RESULT_RE = re.compile(r"합격|결과|서류|면접\s*대상", re.I)


def norm(s): return re.sub(r"\s+", " ", str(s or "")).strip()

def iso_date(text):
    m = DATE_RE.search(str(text or ""))
    if not m: return ""
    try: return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
    except ValueError: return ""

def exact_url(idx): return f"{LIST}?{urlencode({'code':'notice0','idx':str(idx),'ptype':'view'})}"

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
    # Parameter insertion order intentionally mirrors the successful diagnostic probe.
    r = get(session, LIST, params={"page": page, "code": "notice0"})
    soup = BeautifulSoup(r.text, "html.parser")
    found = {}
    for a in soup.find_all("a", href=True):
        title = norm(a.get_text(" ", strip=True))
        href = urljoin(r.url, a.get("href") or "")
        q = parse_qs(urlparse(href).query)
        idx = str((q.get("idx") or [""])[0]); ptype = str((q.get("ptype") or [""])[0])
        if not (idx.isdigit() and ptype == "view" and title): continue
        parent = a.find_parent("tr") or a.parent
        row_text = norm(parent.get_text(" ", strip=True) if parent else title)
        registered = ""
        for yy, mm, dd in DATE_RE.findall(row_text):
            try: registered = datetime(int(yy), int(mm), int(dd)).date().isoformat(); break
            except ValueError: pass
        found[idx] = {"idx": idx, "title": title, "registered": registered}
    return list(found.values())

def detail(session, meta):
    idx = meta["idx"]; url = exact_url(idx); r = get(session, url)
    q = parse_qs(urlparse(r.url).query); final_idx = str((q.get("idx") or [idx])[0])
    text = norm(BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True))
    if final_idx != idx or meta["title"] not in text or "광진청소년센터" not in text:
        raise RuntimeError(f"detail identity mismatch idx={idx} final={final_idx}")
    apply_end = iso_date(meta["title"].split("~", 1)[-1] if "~" in meta["title"] else "")
    if not apply_end:
        for pat in (r"(?:접수|지원|서류\s*접수)[^20]{0,40}(20\d{2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2})", r"(?:마감|까지)[^20]{0,25}(20\d{2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2})"):
            m = re.search(pat, text, re.I)
            if m:
                apply_end = iso_date(m.group(1))
                if apply_end: break
    subject_terms = [term for term in ("로봇","서예","한문","음악","미술","체육","연극","무용","국악","오케스트라","합창","방과후","늘봄") if term in text]
    return {"sourceIdentity":f"seekle:{idx}","source":"시립광진청소년센터","sourceSurface":"culture-arts","sourceSurfaceLabel":"광진청소년센터 강사채용","province":"서울","region":"광진구","regions":["광진구"],"provinces":["서울"],"location":"서울 광진구","title":meta["title"],"subject":" · ".join(subject_terms),"registered":meta.get("registered") or "","applyEnd":apply_end,"originalUrl":url,"url":url,"detailLinkVerified":True}

def main():
    session=requests.Session(); errors=[]; all_rows={}; page_sets=[]
    for page in range(1, MAX_PAGES+1):
        rows=list_rows(session,page); ids={x["idx"] for x in rows}
        if not ids:
            if page==1: errors.append("first list page had no stable detail IDs")
            break
        page_sets.append(ids)
        for row in rows: all_rows[row["idx"]]=row
    traversal_complete=bool(page_sets) and len(set().union(*page_sets))>len(page_sets[0])
    candidates=[r for r in all_rows.values() if INSTRUCTOR_RE.search(r["title"]) and not RESULT_RE.search(r["title"])]
    jobs=[]
    for row in candidates:
        try: jobs.append(detail(session,row))
        except Exception as exc: errors.append(f"{row['idx']}: {exc}")
    current_target=next((j for j in jobs if j["sourceIdentity"]=="seekle:21827"),None)
    if not current_target: errors.append("current live instructor posting seekle:21827 missing")
    elif not (current_target.get("applyEnd")=="2026-09-18" and "강사" in current_target.get("title","")): errors.append("seekle:21827 content/deadline mismatch")
    healthy=traversal_complete and bool(jobs) and not errors
    report={"generatedAt":datetime.now(KST).isoformat(timespec="seconds"),"source":"시립광진청소년센터","publicationEnabled":False,"healthy":healthy,"traversalComplete":traversal_complete,"pagesScanned":len(page_sets),"sourceIdCount":len(all_rows),"candidateIdCount":len(candidates),"jobCount":len(jobs),"missingAfterCount":0 if healthy else 1,"detailErrorCount":len(errors),"errors":errors}
    Path("seekle_reconciliation_report.candidate.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    Path("seekle_jobs.candidate.json").write_text(json.dumps(jobs,ensure_ascii=False,indent=2),encoding="utf-8")
    if not healthy: raise SystemExit("Seekle collector fail-closed: "+"; ".join(errors or ["unhealthy traversal"]))
    canonical=dict(report); canonical["publicationEnabled"]=True
    Path("seekle_jobs.json").write_text(json.dumps(jobs,ensure_ascii=False,indent=2),encoding="utf-8")
    Path("seekle_reconciliation_report.json").write_text(json.dumps(canonical,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(canonical,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
