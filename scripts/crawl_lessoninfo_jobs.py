#!/usr/bin/env python3
"""Collect recruitment-only postings from Lessoninfo with ID reconciliation.

Policy:
- recruitment postings only; exclude sale/trade/rental/community/resume content
- stable identity = bo_table + wr_no
- never infer duplicates from title similarity
- do not replace a known-good dataset when source traversal is incomplete
- respect robots.txt; fail closed when crawling is disallowed
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "lessoninfo_jobs.json"
LEDGER = ROOT / "lessoninfo_source_id_ledger.json"
REPORT = ROOT / "lessoninfo_reconciliation_report.json"
STATE = ROOT / "lessoninfo_collection_state.json"
BASES = ["https://lessoninfo.co.kr", "https://www.lessoninfo.co.kr"]
USER_AGENT = "MetroEduJob-Lessoninfo-Recruitment-Monitor/1.0 (+https://puzzlex5.github.io/gg-edujob/)"
KST = timezone(timedelta(hours=9))
LOOKBACK_DAYS = int(os.getenv("LESSONINFO_LOOKBACK_DAYS", "120"))
MAX_PAGES = int(os.getenv("LESSONINFO_MAX_PAGES", "500"))
DELAY = float(os.getenv("LESSONINFO_DELAY_SECONDS", "0.8"))

# Known recruitment-oriented boards/categories observed on public Lessoninfo URLs.
# The crawler also follows recruitment-looking category/list links discovered from these pages.
SEEDS = [
    {"bo_table": "20161206194557_6971", "code": "20170307205423_8969", "label": "구인"},
    {"bo_table": "20161206194557_6971", "code": "20170307205448_7050", "label": "강사구인"},
    {"bo_table": "20161206194557_6971", "code": "20170506204200_6669", "label": "학원강사구인"},
    {"bo_table": "20161206194557_6971", "code": "20190306172037_6105", "label": "구인"},
    {"bo_table": "20161206194557_6971", "code": "20170316170927_9170", "label": "학교·방과후·늘봄"},
    {"bo_table": "20170316171033_6494", "code": "20170316170927_9170", "label": "학교·방과후·늘봄"},
    {"bo_table": "20161022171024_9316", "code": "20170307205530_6024", "label": "음악단체·기관"},
]

RECRUIT_RE = re.compile(r"구인|채용|모집|강사|선생님|교사|파트|전임|방과후|늘봄|연주자|단원|지휘|반주|교원", re.I)
EXCLUDE_RE = re.compile(r"매매|팝니다|판매|삽니다|양도|인수|권리금|임대|대여|연습실|악기매매|중고악기|구직|레슨받|레슨구|학생모집|원생모집|홍보|콩쿠르|콩쿨|공연정보", re.I)
SEOUL_GYEONGGI_RE = re.compile(r"서울|경기|고양|과천|광명|광주|구리|군포|김포|남양주|동두천|부천|성남|수원|시흥|안산|안성|안양|양주|양평|여주|연천|오산|용인|의왕|의정부|이천|파주|평택|포천|하남|화성|가평")
DATE_RE = re.compile(r"(?:(20\d{2})[.\-/년]\s*)?(\d{1,2})[.\-/월]\s*(\d{1,2})")


def now_kst() -> datetime:
    return datetime.now(KST)


def session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, connect=4, read=4, status=4, backoff_factor=0.8,
                  status_forcelist=(408, 425, 429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET"]))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5"})
    return s


def check_robots(s: requests.Session, base: str) -> tuple[bool, str]:
    url = base.rstrip("/") + "/robots.txt"
    try:
        r = s.get(url, timeout=20)
        if r.status_code == 404:
            return True, "robots_not_found"
        if r.status_code >= 400:
            return False, f"robots_http_{r.status_code}"
        rp = RobotFileParser()
        rp.set_url(url)
        rp.parse(r.text.splitlines())
        allowed = rp.can_fetch(USER_AGENT, base.rstrip("/") + "/board/board.php")
        return allowed, "allowed" if allowed else "disallowed"
    except Exception as e:
        return False, f"robots_error:{type(e).__name__}"


def get(s: requests.Session, url: str) -> requests.Response:
    r = s.get(url, timeout=(10, 35))
    r.raise_for_status()
    if DELAY:
        time.sleep(DELAY)
    return r


def stable_id(url: str) -> str | None:
    q = parse_qs(urlparse(url).query)
    bt = (q.get("bo_table") or [""])[0]
    wr = (q.get("wr_no") or [""])[0]
    if bt and wr.isdigit():
        return f"{bt}:{wr}"
    return None


def normalized_detail(url: str, base: str) -> str | None:
    u = urljoin(base, url)
    p = urlparse(u)
    q = parse_qs(p.query)
    bt = (q.get("bo_table") or [""])[0]
    wr = (q.get("wr_no") or [""])[0]
    if not bt or not wr.isdigit():
        return None
    params = {"bo_table": bt, "wr_no": wr}
    for k in ("board_code", "code"):
        v = (q.get(k) or [""])[0]
        if v:
            params[k] = v
    return f"{p.scheme or 'https'}://{p.netloc or urlparse(base).netloc}/board/board.php?{urlencode(params)}"


def extract_date(text: str) -> str:
    m = DATE_RE.search(text or "")
    if not m:
        return ""
    y = int(m.group(1) or now_kst().year)
    mo, d = int(m.group(2)), int(m.group(3))
    try:
        dt = datetime(y, mo, d, tzinfo=KST)
        if not m.group(1) and dt > now_kst() + timedelta(days=7):
            dt = dt.replace(year=y - 1)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return ""


def is_recent(date_s: str) -> bool:
    if not date_s:
        return True
    try:
        d = datetime.strptime(date_s, "%Y-%m-%d").replace(tzinfo=KST)
        return d >= now_kst() - timedelta(days=LOOKBACK_DAYS)
    except ValueError:
        return True


def parse_list(html: str, base: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        detail = normalized_detail(a.get("href", ""), base)
        sid = stable_id(detail or "") if detail else None
        if not sid or sid in seen:
            continue
        title = " ".join(a.stripped_strings).strip()
        if len(title) < 2:
            title = a.get("title", "").strip()
        parent = a.find_parent(["tr", "li", "div"])
        row_text = " ".join(parent.stripped_strings) if parent else title
        # Candidate collection is deliberately broad; final inclusion occurs after detail parsing.
        if RECRUIT_RE.search(title + " " + row_text) or not EXCLUDE_RE.search(title + " " + row_text):
            seen.add(sid)
            out.append({"sourceIdentity": sid, "url": detail, "titleHint": title, "rowText": row_text,
                        "registeredHint": extract_date(row_text)})
    return out


def parse_detail(s: requests.Session, item: dict) -> dict | None:
    r = get(s, item["url"])
    soup = BeautifulSoup(r.text, "html.parser")
    title = ""
    for sel in ("h1", "h2", "h3", ".subject", ".title", "title"):
        el = soup.select_one(sel)
        if el:
            t = " ".join(el.stripped_strings).strip()
            if len(t) >= 2:
                title = t
                break
    if not title:
        title = item.get("titleHint", "")
    text = " ".join(soup.stripped_strings)
    signal = (title + " " + text[:10000]).strip()
    # Strongly exclude non-recruitment verticals. A recruitment keyword is required.
    if EXCLUDE_RE.search(title) and not RECRUIT_RE.search(title):
        return None
    if not RECRUIT_RE.search(signal):
        return None
    # User scope is 수도권: keep only Seoul/Gyeonggi signals. Unknown location is retained for review
    # rather than silently dropped, because omission is more harmful than a review candidate.
    metro = bool(SEOUL_GYEONGGI_RE.search(signal))
    registered = item.get("registeredHint") or extract_date(text)
    if registered and not is_recent(registered):
        return None
    q = parse_qs(urlparse(item["url"]).query)
    bt = (q.get("bo_table") or [""])[0]
    wr = (q.get("wr_no") or [""])[0]
    return {
        "sourceIdentity": f"{bt}:{wr}",
        "source": "레슨인포",
        "sourceType": "민간 구인",
        "trustLevel": "민간출처",
        "category": "private-recruitment",
        "title": title[:300],
        "registered": registered,
        "url": item["url"],
        "originalUrl": item["url"],
        "boTable": bt,
        "wrNo": wr,
        "metroConfirmed": metro,
        "needsLocationReview": not metro,
        "collectedAt": now_kst().isoformat(timespec="seconds"),
    }


def crawl_seed(s: requests.Session, base: str, seed: dict) -> tuple[list[dict], dict]:
    found: dict[str, dict] = {}
    page_fingerprints = set()
    complete = False
    stop_reason = "max_pages"
    pages = 0
    old_page_streak = 0
    for page in range(1, MAX_PAGES + 1):
        params = {"bo_table": seed["bo_table"], "board_code": "20130529164321_7227",
                  "code": seed.get("code", ""), "page": page}
        url = base.rstrip("/") + "/board/board.php?" + urlencode(params)
        r = get(s, url)
        pages += 1
        rows = parse_list(r.text, base)
        ids = tuple(sorted(x["sourceIdentity"] for x in rows))
        if not rows:
            complete, stop_reason = True, "no_rows"
            break
        fp = ids[:50]
        if fp in page_fingerprints:
            complete, stop_reason = True, "repeated_page"
            break
        page_fingerprints.add(fp)
        before = len(found)
        for row in rows:
            found.setdefault(row["sourceIdentity"], row)
        if len(found) == before:
            complete, stop_reason = True, "no_new_ids"
            break
        dated = [x["registeredHint"] for x in rows if x.get("registeredHint")]
        if dated and all(not is_recent(d) for d in dated):
            old_page_streak += 1
            if old_page_streak >= 2:
                complete, stop_reason = True, "lookback_crossed"
                break
        else:
            old_page_streak = 0
    return list(found.values()), {"label": seed["label"], "boTable": seed["bo_table"], "code": seed.get("code", ""),
                                  "pagesScanned": pages, "discoveredIds": len(found), "complete": complete,
                                  "stopReason": stop_reason}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    s = session()
    base = None
    robots_status = ""
    for candidate in BASES:
        allowed, status = check_robots(s, candidate)
        if allowed:
            try:
                r = get(s, candidate + "/")
                if r.ok:
                    base, robots_status = candidate, status
                    break
            except Exception:
                pass
        else:
            robots_status = status
    if not base:
        report = {"generatedAt": now_kst().isoformat(timespec="seconds"), "healthy": False,
                  "blocker": "Lessoninfo is unavailable or robots policy does not allow this collector",
                  "robotsStatus": robots_status, "preservedPreviousDataset": OUT.exists()}
        write_json(REPORT, report)
        return 2

    all_candidates: dict[str, dict] = {}
    source_reports = []
    traversal_complete = True
    errors = []
    for seed in SEEDS:
        try:
            rows, sr = crawl_seed(s, base, seed)
            source_reports.append(sr)
            traversal_complete &= bool(sr["complete"])
            for row in rows:
                all_candidates.setdefault(row["sourceIdentity"], row)
        except Exception as e:
            traversal_complete = False
            source_reports.append({**seed, "complete": False, "error": f"{type(e).__name__}: {e}"})
            errors.append(f"{seed['bo_table']}:{seed.get('code','')}:{type(e).__name__}")

    previous = read_json(OUT, {"jobs": []})
    prev_jobs = previous if isinstance(previous, list) else previous.get("jobs", [])
    prev_by_id = {j.get("sourceIdentity"): j for j in prev_jobs if j.get("sourceIdentity")}
    jobs_by_id: dict[str, dict] = dict(prev_by_id)
    parsed_now = 0
    rejected_non_recruitment = 0
    detail_errors = []
    for sid, candidate in all_candidates.items():
        # Re-fetch newly seen IDs and currently visible IDs so edits/deletions are observed.
        try:
            job = parse_detail(s, candidate)
            if job:
                jobs_by_id[sid] = job
                parsed_now += 1
            else:
                rejected_non_recruitment += 1
        except Exception as e:
            detail_errors.append({"id": sid, "error": type(e).__name__})
            if sid not in prev_by_id:
                traversal_complete = False

    discovered_ids = set(all_candidates)
    dataset_ids = set(jobs_by_id)
    missing_after = sorted(discovered_ids - dataset_ids)
    healthy = traversal_complete and not missing_after and not detail_errors

    # Important safety rule: publish only when discovery/reconciliation is healthy.
    if healthy:
        # Keep prior IDs as history but only surface recent or currently rediscovered rows.
        jobs = sorted(jobs_by_id.values(), key=lambda j: (j.get("registered") or "", j.get("sourceIdentity") or ""), reverse=True)
        write_json(OUT, {"updatedAt": now_kst().strftime("%Y-%m-%d %H:%M KST"), "source": "레슨인포",
                         "category": "private-recruitment", "jobs": jobs})
    else:
        jobs = prev_jobs

    ledger = read_json(LEDGER, {"ids": {}, "snapshots": []})
    ledger.setdefault("ids", {})
    for sid, c in all_candidates.items():
        ent = ledger["ids"].setdefault(sid, {"firstSeenAt": now_kst().isoformat(timespec="seconds")})
        ent["lastSeenAt"] = now_kst().isoformat(timespec="seconds")
        ent["url"] = c["url"]
    ledger.setdefault("snapshots", []).append({"at": now_kst().isoformat(timespec="seconds"),
                                               "discoveredIds": len(discovered_ids), "healthy": healthy})
    ledger["snapshots"] = ledger["snapshots"][-120:]
    write_json(LEDGER, ledger)

    report = {
        "generatedAt": now_kst().isoformat(timespec="seconds"),
        "policy": "lessoninfo-recruitment-only-stable-id-v1",
        "lookbackDays": LOOKBACK_DAYS,
        "base": base,
        "robotsStatus": robots_status,
        "healthy": healthy,
        "traversalComplete": traversal_complete,
        "sourceReports": source_reports,
        "discoveredIdCount": len(discovered_ids),
        "publishedIdCount": len({j.get('sourceIdentity') for j in jobs if j.get('sourceIdentity')}),
        "missingAfterCount": len(missing_after),
        "missingAfterExamples": missing_after[:20],
        "parsedNow": parsed_now,
        "rejectedNonRecruitment": rejected_non_recruitment,
        "detailErrorCount": len(detail_errors),
        "detailErrorExamples": detail_errors[:10],
        "preservedPreviousDataset": not healthy,
        "errors": errors,
    }
    write_json(REPORT, report)
    write_json(STATE, {"updatedAt": now_kst().isoformat(timespec="seconds"), "healthy": healthy,
                       "lastGoodAt": now_kst().isoformat(timespec="seconds") if healthy else read_json(STATE, {}).get("lastGoodAt")})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if healthy else 3


if __name__ == "__main__":
    raise SystemExit(main())
