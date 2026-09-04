#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
API = "https://prod-backend.00gangsa.com/v1/recruitments"
BASE = "https://00gangsa.com"
PAGE_SIZE = 100
MAX_RETRIES = 4
MAX_CATCHUP_ROUNDS = 4
DROP_CRITICAL_RATIO = 0.65
ALLOWED_PROVINCES = {"서울", "경기"}

CANDIDATE_JOBS = Path("gonggonggangsa_jobs.candidate.json")
CANDIDATE_LEDGER = Path("gonggonggangsa_source_id_ledger.candidate.json")
CANDIDATE_STATE = Path("gonggonggangsa_collection_state.candidate.json")
CANDIDATE_REPORT = Path("gonggonggangsa_reconciliation_report.candidate.json")
CANDIDATE_DETAIL = Path("gonggonggangsa_detail_link_report.candidate.json")

CANONICAL_JOBS = Path("gonggonggangsa_jobs.json")
CANONICAL_LEDGER = Path("gonggonggangsa_source_id_ledger.json")
CANONICAL_STATE = Path("gonggonggangsa_collection_state.json")
CANONICAL_REPORT = Path("gonggonggangsa_reconciliation_report.json")
CANONICAL_DETAIL = Path("gonggonggangsa_detail_link_report.json")

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "User-Agent": "MetroEduJob/1.0",
    "Origin": BASE,
    "Referer": BASE + "/",
}


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_dt(value):
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            cut = 19 if "%H" in fmt else 10
            return datetime.strptime(raw[:cut], fmt).replace(tzinfo=KST)
        except ValueError:
            continue
    return None


def request_page(session: requests.Session, page: int, size: int = PAGE_SIZE) -> dict:
    body = {"page": page, "size": size, "order": "RECENT"}
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            r = session.post(API, json=body, headers=HEADERS, timeout=30)
            r.raise_for_status()
            payload = r.json()
            data = payload.get("data") or {}
            rows = data.get("list")
            if not isinstance(rows, list):
                raise RuntimeError("API response missing data.list")
            if int(data.get("currentPage") or 0) != page:
                raise RuntimeError(f"currentPage mismatch: requested={page} got={data.get('currentPage')}")
            return data
        except Exception as exc:
            last_exc = exc
            if attempt + 1 < MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"page {page} failed after retries: {type(last_exc).__name__}: {last_exc}")


def row_id(row):
    sid = str((row or {}).get("id") or "")
    return sid if sid.isdigit() else ""


def ingest_rows(rows, target, *, error_prefix, errors):
    new_ids = []
    duplicates = 0
    invalid = 0
    for row in rows:
        sid = row_id(row)
        if not sid:
            invalid += 1
            errors.append(f"{error_prefix}: invalid row id {sid!r}")
            continue
        if sid in target:
            duplicates += 1
        else:
            new_ids.append(sid)
        target[sid] = row
    return new_ids, duplicates, invalid


def provinces(row):
    out = []
    for reg in row.get("regions") or []:
        if not isinstance(reg, dict):
            continue
        p = (reg.get("province") or {}).get("name")
        if p:
            out.append(str(p))
    return list(dict.fromkeys(out))


def cities(row):
    out = []
    for reg in row.get("regions") or []:
        if not isinstance(reg, dict):
            continue
        p = str((reg.get("province") or {}).get("name") or "")
        c = str((reg.get("city") or {}).get("name") or "")
        if p in ALLOWED_PROVINCES and c:
            out.append(c)
    return list(dict.fromkeys(out))


def is_current_metro(row):
    if str(row.get("state") or "") != "RECRUITING":
        return False
    ps = set(provinces(row))
    if not (ps & ALLOWED_PROVINCES):
        return False
    start = parse_dt(row.get("recruitmentAt"))
    if start and start > NOW:
        return False
    deadline = parse_dt(row.get("deadlineAt"))
    if deadline and deadline < NOW:
        return False
    return True


def normalize(row):
    sid_num = row_id(row)
    sid = f"gonggonggangsa:{sid_num}"
    ps = [p for p in provinces(row) if p in ALLOWED_PROVINCES]
    cs = cities(row)
    fields = [str(x.get("name") or "") for x in (row.get("fields") or []) if isinstance(x, dict) and x.get("name")]
    institution = row.get("institution") or {}
    deadline = parse_dt(row.get("deadlineAt"))
    registered = parse_dt(row.get("recruitmentAt"))
    province = ps[0] if ps else ""
    location_bits = list(ps) + cs
    detail_url = f"{BASE}/recruitments/{sid_num}"
    return {
        "sourceIdentity": sid,
        "source": "공공강사",
        "sourceType": "민간 구인",
        "sourceSurface": "public-instructor",
        "sourceSurfaceLabel": "공공강사",
        "province": province,
        "provinces": ps,
        "region": cs[0] if len(cs) == 1 else ", ".join(cs),
        "regions": cs,
        "location": " ".join(dict.fromkeys(location_bits)),
        "school": str(institution.get("name") or "공공강사 구인"),
        "title": str(row.get("title") or "").strip(),
        "subject": " · ".join(fields),
        "type": "시간강사/강사",
        "applyStart": registered.strftime("%Y-%m-%d") if registered else "",
        "applyEnd": deadline.strftime("%Y-%m-%d") if deadline else "",
        "registered": registered.strftime("%Y-%m-%d") if registered else "",
        "url": detail_url,
        "boardUrl": f"{BASE}/recruitments",
        "openUrl": detail_url,
        "openMethod": "GET",
        "stableSourceId": sid_num,
        "sourceState": str(row.get("state") or ""),
        "raw": row,
    }


def validate_detail(url: str):
    try:
        r = requests.get(url, headers={"User-Agent": "MetroEduJob/1.0", "Accept": "text/html,*/*"}, timeout=20, allow_redirects=True)
        ok = r.status_code == 200 and "/recruitments/" in r.url
        return {"url": url, "ok": ok, "status": r.status_code, "finalUrl": r.url, "error": None if ok else "unexpected status or redirect"}
    except Exception as exc:
        return {"url": url, "ok": False, "status": None, "finalUrl": None, "error": f"{type(exc).__name__}: {exc}"}


def catch_up_live_head(session, all_rows_by_id, start_count, errors):
    """Recover rows inserted at the live feed head during a full traversal.

    Walk from page 1 until an all-known page proves overlap with the completed snapshot,
    then require the ID union to equal a stable server count exactly. Growth is recovered,
    never merely tolerated.
    """
    rounds = []
    final_count = start_count
    for round_no in range(1, MAX_CATCHUP_ROUNDS + 1):
        head = request_page(session, 1)
        observed_count = int(head.get("count") or 0)
        observed_pages = int(head.get("totalPage") or 0)
        if observed_count < start_count:
            errors.append(f"source count dropped during traversal: {start_count} -> {observed_count}")
            return rounds, observed_count, False

        new_in_round = []
        pages_scanned = 0
        overlap_reached = False
        page_no = 1
        while page_no <= max(1, observed_pages):
            data = head if page_no == 1 else request_page(session, page_no)
            rows = data.get("list") or []
            before = set(all_rows_by_id)
            new_ids, _, _ = ingest_rows(rows, all_rows_by_id, error_prefix=f"catchup round {round_no} page {page_no}", errors=errors)
            new_in_round.extend(new_ids)
            pages_scanned += 1
            valid_ids = [row_id(r) for r in rows if row_id(r)]
            if not rows or (valid_ids and all(sid in before for sid in valid_ids)):
                overlap_reached = True
                break
            page_no += 1

        verify = request_page(session, 1)
        final_count = int(verify.get("count") or 0)
        rounds.append({
            "round": round_no,
            "serverCountBefore": observed_count,
            "serverCountAfter": final_count,
            "pagesScanned": pages_scanned,
            "newIdCount": len(set(new_in_round)),
            "newIds": sorted(set(new_in_round), key=lambda x: int(x), reverse=True)[:200],
            "overlapReached": overlap_reached,
            "unionCount": len(all_rows_by_id),
        })
        if final_count < start_count:
            errors.append(f"source count dropped during catch-up: {start_count} -> {final_count}")
            return rounds, final_count, False
        if overlap_reached and final_count == observed_count and len(all_rows_by_id) == final_count:
            return rounds, final_count, True
        if len(all_rows_by_id) > final_count:
            errors.append(f"source IDs exceed current server count during catch-up: ids={len(all_rows_by_id)} server={final_count}")
            return rounds, final_count, False

    errors.append(f"live head did not stabilize after {MAX_CATCHUP_ROUNDS} catch-up rounds: ids={len(all_rows_by_id)} server={final_count}")
    return rounds, final_count, False


def main() -> int:
    generated = NOW.isoformat(timespec="seconds")
    previous_jobs = load_json(CANONICAL_JOBS, [])
    previous_rows = previous_jobs if isinstance(previous_jobs, list) else previous_jobs.get("jobs", []) if isinstance(previous_jobs, dict) else []
    previous_count = len(previous_rows)
    previous_ledger = load_json(CANONICAL_LEDGER, {"entries": {}})
    if not isinstance(previous_ledger, dict):
        previous_ledger = {"entries": {}}
    previous_ledger.setdefault("entries", {})

    errors = []
    pages = []
    catchup_rounds = []
    all_rows_by_id = {}
    traversal_complete = False
    initial_count = None
    count_after_full_scan = None
    final_count = None
    total_pages = None

    session = requests.Session()
    try:
        first = request_page(session, 1)
        initial_count = int(first.get("count") or 0)
        total_pages = int(first.get("totalPage") or 0)
        expected_pages = math.ceil(initial_count / PAGE_SIZE) if initial_count else 0
        if total_pages != expected_pages:
            raise RuntimeError(f"totalPage inconsistent: api={total_pages} expected={expected_pages}")
        if initial_count <= 0:
            raise RuntimeError("source returned abnormal zero count")

        snapshot_duplicate_ids = 0
        for page_no in range(1, total_pages + 1):
            data = first if page_no == 1 else request_page(session, page_no)
            rows = data.get("list") or []
            new_ids, duplicate_ids, _ = ingest_rows(rows, all_rows_by_id, error_prefix=f"page {page_no}", errors=errors)
            snapshot_duplicate_ids += duplicate_ids
            pages.append({
                "page": page_no,
                "rowCount": len(rows),
                "newIdCount": len(new_ids),
                "duplicateIdCount": duplicate_ids,
                "firstId": row_id(rows[0]) if rows else None,
                "lastId": row_id(rows[-1]) if rows else None,
            })

        post_scan = request_page(session, 1)
        count_after_full_scan = int(post_scan.get("count") or 0)
        catchup_rounds, final_count, catchup_complete = catch_up_live_head(session, all_rows_by_id, initial_count, errors)
        traversal_complete = bool(
            not errors
            and len(pages) == total_pages
            and snapshot_duplicate_ids == 0
            and catchup_complete
            and final_count is not None
            and len(all_rows_by_id) == final_count
        )
        if snapshot_duplicate_ids:
            errors.append(f"full traversal had {snapshot_duplicate_ids} duplicate page-boundary IDs")
        if not traversal_complete and not errors:
            errors.append(f"exhaustive traversal proof failed: ids={len(all_rows_by_id)} finalServerCount={final_count}")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    target_rows = [row for row in all_rows_by_id.values() if is_current_metro(row)] if traversal_complete else []
    jobs = [normalize(row) for row in target_rows]
    jobs.sort(key=lambda x: (x.get("registered") or "", x.get("sourceIdentity") or ""), reverse=True)
    discovered_ids = {x["sourceIdentity"] for x in jobs}
    stored_ids = {x.get("sourceIdentity") for x in jobs if x.get("sourceIdentity")}
    missing_after = sorted(discovered_ids - stored_ids)

    contamination = [x["sourceIdentity"] for x in jobs if not set(x.get("provinces") or []) or not set(x.get("provinces") or []).issubset(ALLOWED_PROVINCES)]
    state_errors = [x["sourceIdentity"] for x in jobs if str(x.get("sourceState")) != "RECRUITING"]
    expired = []
    future = []
    for x in jobs:
        raw = x.get("raw") or {}
        d = parse_dt(raw.get("deadlineAt"))
        s = parse_dt(raw.get("recruitmentAt"))
        if d and d < NOW:
            expired.append(x["sourceIdentity"])
        if s and s > NOW:
            future.append(x["sourceIdentity"])

    detail_results = []
    if traversal_complete and jobs:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(validate_detail, x["url"]): x["sourceIdentity"] for x in jobs}
            for fut in as_completed(futures):
                sid = futures[fut]
                try:
                    result = fut.result()
                except Exception as exc:
                    result = {"url": None, "ok": False, "status": None, "finalUrl": None, "error": f"{type(exc).__name__}: {exc}"}
                result["sourceIdentity"] = sid
                detail_results.append(result)
    detail_errors = [x for x in detail_results if not x.get("ok")]
    detail_complete = bool(jobs) and len(detail_results) == len(jobs) and not detail_errors

    drop_ratio = 0.0
    abnormal_drop = False
    if previous_count > 0 and len(jobs) < previous_count:
        drop_ratio = (previous_count - len(jobs)) / previous_count
        abnormal_drop = drop_ratio >= DROP_CRITICAL_RATIO

    healthy = bool(traversal_complete and jobs and not errors and not missing_after and not contamination and not state_errors and not expired and not future and detail_complete and not abnormal_drop)

    ledger_entries = dict(previous_ledger.get("entries") or {})
    for x in jobs:
        sid = x["sourceIdentity"]
        old = ledger_entries.get(sid) if isinstance(ledger_entries.get(sid), dict) else {}
        ledger_entries[sid] = {
            **old,
            "sourceIdentity": sid,
            "url": x["url"],
            "title": x["title"],
            "firstSeenAt": old.get("firstSeenAt") or generated,
            "lastSeenAt": generated,
            "presentInLatestScan": True,
        }
    for sid, ent in list(ledger_entries.items()):
        if isinstance(ent, dict) and sid not in discovered_ids:
            ent["presentInLatestScan"] = False

    candidate_state = {
        "generatedAt": generated,
        "source": "공공강사",
        "currentMetroCount": len(jobs),
        "allSourceIdCount": len(all_rows_by_id),
        "serverCountAtStart": initial_count,
        "serverCountAtEnd": final_count,
        "totalPages": total_pages,
        "traversalComplete": traversal_complete,
        "healthy": healthy,
    }
    candidate_report = {
        "generatedAt": generated,
        "source": "공공강사",
        "policy": "gonggonggangsa-post-api-live-head-catchup-v2",
        "publicationEnabled": False,
        "healthy": healthy,
        "traversalComplete": traversal_complete,
        "serverCountAtStart": initial_count,
        "serverCountAfterFullScan": count_after_full_scan,
        "serverCountAtEnd": final_count,
        "countGrowthDuringScan": (final_count - initial_count) if isinstance(final_count, int) and isinstance(initial_count, int) else None,
        "totalPages": total_pages,
        "pagesVisited": len(pages),
        "headCatchupRounds": catchup_rounds,
        "headCatchupNewIdCount": sum(int(x.get("newIdCount") or 0) for x in catchup_rounds),
        "allSourceIdCount": len(all_rows_by_id),
        "candidateIdCount": len(discovered_ids),
        "publishedIdCount": len(stored_ids) if healthy else previous_count,
        "missingAfterCount": len(missing_after),
        "missingAfter": missing_after[:200],
        "detailErrorCount": len(detail_errors),
        "regionContaminationCount": len(contamination),
        "stateErrorCount": len(state_errors),
        "expiredCount": len(expired),
        "futureRecruitmentCount": len(future),
        "previousCanonicalCount": previous_count,
        "candidateDropRatio": round(drop_ratio, 6),
        "abnormalDrop": abnormal_drop,
        "errors": errors,
        "pages": pages,
        "nextGate": "private multi-source candidate + unified validator" if healthy else "keep prior canonical data; diagnose candidate failure",
    }
    detail_report = {
        "generatedAt": generated,
        "source": "공공강사",
        "healthy": detail_complete,
        "detailCoverageComplete": detail_complete,
        "candidateCount": len(jobs),
        "checkedCount": len(detail_results),
        "detailErrorCount": len(detail_errors),
        "errors": detail_errors[:200],
    }
    candidate_ledger = {"generatedAt": generated, "source": "공공강사", "entries": ledger_entries}

    write_json(CANDIDATE_JOBS, jobs)
    write_json(CANDIDATE_LEDGER, candidate_ledger)
    write_json(CANDIDATE_STATE, candidate_state)
    write_json(CANDIDATE_REPORT, candidate_report)
    write_json(CANDIDATE_DETAIL, detail_report)

    if healthy:
        canonical_report = dict(candidate_report)
        canonical_report["publicationEnabled"] = True
        canonical_report["publishedIdCount"] = len(jobs)
        write_json(CANONICAL_JOBS, jobs)
        write_json(CANONICAL_LEDGER, candidate_ledger)
        write_json(CANONICAL_STATE, candidate_state)
        write_json(CANONICAL_REPORT, canonical_report)
        write_json(CANONICAL_DETAIL, detail_report)

    print(json.dumps({
        "healthy": healthy,
        "traversalComplete": traversal_complete,
        "serverCountAtStart": initial_count,
        "serverCountAtEnd": final_count,
        "allSourceIdCount": len(all_rows_by_id),
        "candidateIdCount": len(jobs),
        "missingAfterCount": len(missing_after),
        "detailErrorCount": len(detail_errors),
        "headCatchupNewIdCount": sum(int(x.get("newIdCount") or 0) for x in catchup_rounds),
        "abnormalDrop": abnormal_drop,
        "errors": errors[:10],
    }, ensure_ascii=False))
    return 0 if healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
