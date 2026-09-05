#!/usr/bin/env python3
"""Recover official Seoul support-office recruitment detail posts that live outside FUS/JOL11.

This is deliberately conservative: it only accepts recent official same-host CMS detail pages
surfaced by either the general board discovery or the verified deep CMS list traversal, requires
explicit recruitment language, and rejects result/status/personnel notices. Existing jobs always
win. The deep discovery is an alternative board surface of an existing support office, not a new
conceptual official source.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs.json"
REPORT = ROOT / "board_discovery_report.json"
DEEP_REPORT = ROOT / "seoul_cms_deep_discovery_report.json"
OUT_REPORT = ROOT / "seoul_office_cms_recovery_report.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)

DETAIL_RE = re.compile(r"/CMS/.+/\d+_\d+\.html$", re.I)
RECRUIT_RE = re.compile(r"채용|구인|모집|기간제|계약제|전문상담사|교육공무직|대체인력|대체직원|근로자|업무보조원|인력풀|강사|교원|교사|전담조사관", re.I)
# Status/result notices often contain the word '채용' but are not recruitment opportunities.
# Keep status phrases specific enough that recruitment notices mentioning 서류 제출 are not lost.
EXCLUDE_RE = re.compile(
    r"합격자|합격\s*명단|명단\s*공개|응시\s*현황|응시원서\s*접수\s*현황|원서\s*접수\s*현황|접수\s*현황|"
    r"서류\s*(?:심사|전형)\s*(?:합격|결과|대상)?|면접\s*(?:대상|일정|결과)|채용\s*(?:결과|완료|취소)|"
    r"선정\s*결과|최종\s*결과|인사발령|보도자료|경쟁률|접수\s*인원|응시\s*인원",
    re.I,
)
DATE_RE = re.compile(r"(20\d{2})[./-]\s*(\d{1,2})[./-]\s*(\d{1,2})")

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (compatible; metro-edujob-seoul-cms-recovery/1.1)", "Accept-Language": "ko-KR,ko;q=0.9"})
retry = Retry(total=3, connect=3, read=3, status=3, backoff_factor=0.8,
              status_forcelist=(408,429,500,502,503,504), allowed_methods=frozenset(("GET",)),
              respect_retry_after_header=True, raise_on_status=False)
S.mount("https://", HTTPAdapter(max_retries=retry)); S.mount("http://", HTTPAdapter(max_retries=retry))


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def norm(s):
    return re.sub(r"[^0-9a-z가-힣]+", "", clean(s).lower())


def date_norm(text):
    m = DATE_RE.search(text or "")
    return f"{int(m.group(1)):04d}/{int(m.group(2)):02d}/{int(m.group(3)):02d}" if m else ""


def recent(ds):
    if not ds:
        return False
    try:
        d = datetime.strptime(ds, "%Y/%m/%d").replace(tzinfo=KST)
        return NOW - timedelta(days=90) <= d <= NOW + timedelta(days=1)
    except Exception:
        return False


def guess_type(text):
    if re.search(r"교육공무직|대체직원|대체인력|근로자|조리실무|업무보조원", text): return "교육공무직/기간제근로자"
    if re.search(r"강사|튜터|조사관", text): return "시간강사/강사"
    if re.search(r"기간제|계약제|교원|교사|전문상담", text): return "기간제교원"
    return "기타"


def fetch_job(office, item):
    url = item.get("url", "")
    title_hint = clean(item.get("text", ""))
    if not DETAIL_RE.search(urlparse(url).path):
        return None, "not-cms-detail"
    if not RECRUIT_RE.search(title_hint) or EXCLUDE_RE.search(title_hint):
        return None, "title-filter"
    try:
        r = S.get(url, timeout=18, allow_redirects=True)
        if r.status_code >= 400:
            return None, f"http-{r.status_code}"
        if not r.encoding or r.encoding.lower() == "iso-8859-1": r.encoding = r.apparent_encoding or "utf-8"
    except Exception as e:
        return None, type(e).__name__
    soup = BeautifulSoup(r.text, "html.parser")
    body = clean(soup.get_text(" ", strip=True))
    title = title_hint
    for sel in ("h2", "h3", ".view-title", ".title", "title"):
        el = soup.select_one(sel)
        t = clean(el.get_text(" ", strip=True)) if el else ""
        if len(t) >= 6 and RECRUIT_RE.search(t) and not EXCLUDE_RE.search(t):
            title = t; break
    if not RECRUIT_RE.search(title + " " + body[:1200]) or EXCLUDE_RE.search(title):
        return None, "content-filter"
    registered = ""
    for pat in (r"작성일\s*[:|]?\s*(20\d{2}[./-]\s*\d{1,2}[./-]\s*\d{1,2})",
                r"등록일\s*[:|]?\s*(20\d{2}[./-]\s*\d{1,2}[./-]\s*\d{1,2})"):
        m = re.search(pat, body)
        if m: registered = date_norm(m.group(1)); break
    if not registered:
        # Deep-list discovery supplies posting-specific list dates. Use them only when the detail
        # page has no explicit registered/written label; never use crawl time as a substitute.
        registered = date_norm(item.get("registered", "")) or date_norm(body)
    if not recent(registered):
        return None, "not-recent"
    return {
        "id": "sen-office-cms-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:20],
        "province": "서울", "school": office, "title": title, "subject": "",
        "region": "", "regions": [], "type": guess_type(title + " " + body[:1500]),
        "schoolLevel": "교육행정기관" if "교육지원청" in office else "기타",
        "applyStart": "", "applyEnd": "", "workStart": "", "workEnd": "",
        "registered": registered, "headcount": "", "source": office,
        "checkedSources": [office], "sourceType": "교육지원청 자체 채용공고",
        "url": url, "boardUrl": item.get("boardUrl") or url
    }, "accepted"


def is_recovered_status_notice(job):
    """Remove only records created by this supplemental recovery path that are now known status notices."""
    return str(job.get("id", "")).startswith("sen-office-cms-") and bool(EXCLUDE_RE.search(clean(job.get("title", ""))))


def load_json(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def discovered_offices():
    """Merge shallow and deep discovery candidates by exact detail URL, preserving office identity."""
    merged = {}
    inputs = []
    shallow = load_json(REPORT)
    if shallow:
        inputs.append(REPORT.name)
        for office_row in shallow.get("seoul", []):
            office = office_row.get("name", "")
            if not office:
                continue
            bucket = merged.setdefault(office, {})
            for item in office_row.get("candidates", []):
                url = item.get("url", "")
                if url:
                    bucket.setdefault(url, dict(item))

    deep = load_json(DEEP_REPORT)
    if deep:
        inputs.append(DEEP_REPORT.name)
        if not deep.get("healthy"):
            raise SystemExit("deep Seoul CMS discovery is unhealthy; refusing supplemental recovery")
        for office_row in deep.get("seoul", []):
            office = office_row.get("name", "")
            if not office:
                continue
            bucket = merged.setdefault(office, {})
            for item in office_row.get("candidates", []):
                url = item.get("url", "")
                if not url:
                    continue
                enriched = dict(item)
                enriched.setdefault("boardUrl", office_row.get("boardUrl", ""))
                bucket[url] = enriched

    return [
        {"name": office, "candidates": list(items.values())}
        for office, items in sorted(merged.items())
    ], inputs


def main():
    if not JOBS.exists():
        raise SystemExit("jobs.json missing")
    office_rows, discovery_inputs = discovered_offices()
    if not office_rows:
        raise SystemExit("no Seoul CMS discovery evidence available")
    payload = json.loads(JOBS.read_text(encoding="utf-8"))
    original_jobs = payload.get("jobs", [])
    removed = [j for j in original_jobs if is_recovered_status_notice(j)]
    jobs = [j for j in original_jobs if not is_recovered_status_notice(j)]
    existing_urls = {j.get("url", "") for j in jobs}
    existing_semantic = {(norm(j.get("title", "")), j.get("registered", "")) for j in jobs}
    accepted, skipped = [], []
    for office_row in office_rows:
        office = office_row.get("name", "")
        for item in office_row.get("candidates", []):
            job, reason = fetch_job(office, item)
            if not job:
                skipped.append({"office": office, "url": item.get("url", ""), "reason": reason})
                continue
            semantic = (norm(job["title"]), job["registered"])
            if job["url"] in existing_urls or semantic in existing_semantic:
                skipped.append({"office": office, "url": job["url"], "reason": "already-present"})
                continue
            jobs.append(job); accepted.append(job)
            existing_urls.add(job["url"]); existing_semantic.add(semantic)
    if accepted or removed:
        jobs.sort(key=lambda j: (j.get("registered", ""), j.get("applyEnd", "")), reverse=True)
        payload["jobs"] = jobs
        payload.setdefault("verification", {})["seoulOfficeCmsRecovery"] = {
            "added": len(accepted), "removedStatusNotices": len(removed), "state": "pending-verification",
            "discoveryInputs": discovery_inputs,
        }
        JOBS.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = {"generatedAt": NOW.isoformat(timespec="seconds"), "added": len(accepted),
              "removedStatusNotices": len(removed), "discoveryInputs": discovery_inputs,
              "removed": [{k:v for k,v in j.items() if k in ("source","title","registered","url")} for j in removed],
              "accepted": [{k:v for k,v in j.items() if k in ("source","title","registered","url")} for j in accepted],
              "skippedCount": len(skipped), "skipped": skipped[:300]}
    OUT_REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Seoul office CMS recovery: added={len(accepted)} removed_status={len(removed)} skipped={len(skipped)} inputs={discovery_inputs}")


if __name__ == "__main__":
    main()
