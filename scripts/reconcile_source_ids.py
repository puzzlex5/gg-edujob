#!/usr/bin/env python3
"""Independent official-ID reconciliation for support-office recruitment boards.

The normal crawler can be wrong in two ways even when it reports success: a row can be skipped,
or two different postings with similar titles can be collapsed by presentation-level dedupe.
This audit therefore treats the source system's stable posting ID (Gyeonggi bbsId+nttSn,
Seoul host+job_seq) as the primary evidence.  Every currently discoverable official ID is
compared with jobs.json and an append-only source-id ledger.  Missing official IDs are recovered
as distinct source occurrences before the publication gate runs.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import complete_support_coverage as cov

ROOT = Path(__file__).resolve().parents[1]
JOBS_PATH = ROOT / "jobs.json"
LEDGER_PATH = ROOT / "source_id_ledger.json"
REPORT_PATH = ROOT / "source_reconciliation_report.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
NOW_S = NOW.strftime("%Y-%m-%d %H:%M:%S KST")


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def source_id(job):
    province = str(job.get("province") or "")
    if province == "서울":
        seq = str((job.get("openParams") or {}).get("job_seq") or "")
        host = (urlparse(job.get("openUrl") or job.get("url") or "").hostname or "").lower()
        if not seq.isdigit():
            try:
                seq = (parse_qs(urlparse(job.get("url") or "").query).get("job_seq") or [""])[0]
            except Exception:
                seq = ""
        return f"seoul:{host}:{seq}" if host and seq.isdigit() else ""

    if province == "경기":
        raw = job.get("url") or ""
        try:
            u = urlparse(raw)
            q = parse_qs(u.query)
            bbs = str(job.get("bbsId") or (q.get("bbsId") or [""])[0])
            ntt = str(job.get("nttSn") or (q.get("nttSn") or [""])[0])
            host = (u.hostname or "").lower()
            return f"mircms:{host}:{bbs}:{ntt}" if host and bbs.isdigit() and ntt.isdigit() else ""
        except Exception:
            return ""
    return ""


def job_fingerprint(job):
    def n(v):
        return re.sub(r"[^0-9a-z가-힣]+", "", str(v or "").lower())
    return "|".join((n(job.get("province")), n(job.get("source")), n(job.get("school")), n(job.get("title")), n(job.get("registered"))))


def crawl_official_ids():
    offices = []
    all_rows = []

    for src in cov.SOURCES["gyeonggi"]["supportOffices"]:
        boards = []
        for u in list(src.get("boardUrls", [])) + list(cov.CACHE.get(src["name"], [])):
            if u and u not in boards:
                boards.append(u)
        rows, metas = [], []
        for board in boards:
            found, meta = cov.gyeonggi_board(board, src)
            rows.extend(found)
            metas.append(meta)
        ids = {source_id(x) for x in rows if source_id(x)}
        offices.append({
            "province": "경기", "name": src["name"], "boards": boards,
            "officialIds": sorted(ids), "officialIdCount": len(ids),
            "coverageComplete": bool(metas) and all(m.get("coverageComplete") for m in metas),
            "pagesScanned": sum(int(m.get("pagesScanned") or 0) for m in metas),
            "accessErrors": sum(1 for m in metas if m.get("accessError")),
        })
        all_rows.extend(rows)

    for src in cov.SOURCES["seoul"]["supportOffices"]:
        rows, meta = cov.seoul_board(src)
        ids = {source_id(x) for x in rows if source_id(x)}
        offices.append({
            "province": "서울", "name": src["name"], "boards": [src.get("boardUrl", "")],
            "officialIds": sorted(ids), "officialIdCount": len(ids),
            "coverageComplete": bool(meta.get("coverageComplete")),
            "pagesScanned": int(meta.get("pagesScanned") or 0),
            "accessErrors": 1 if meta.get("accessError") else 0,
        })
        all_rows.extend(rows)

    # Same source ID may appear through menu aliases. Keep one canonical row per source ID.
    by_id = {}
    for row in all_rows:
        sid = source_id(row)
        if sid and sid not in by_id:
            by_id[sid] = row
    return offices, by_id


def main():
    payload = load(JOBS_PATH, {})
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    if len(jobs) < 100:
        raise SystemExit(f"Refusing ID reconciliation against suspicious dataset: {len(jobs)} jobs")

    old_ledger = load(LEDGER_PATH, {"entries": {}})
    entries = old_ledger.get("entries", {}) if isinstance(old_ledger, dict) else {}

    offices, official = crawl_official_ids()
    dataset_ids_before = {source_id(j) for j in jobs if source_id(j)}
    official_ids = set(official)
    missing_before = sorted(official_ids - dataset_ids_before)

    # Recover by exact source identity, never by title. This deliberately prevents distinct
    # re-postings / 1st-2nd notices with similar titles from being collapsed.
    recovered = []
    for sid in missing_before:
        row = dict(official[sid])
        row["recoveredBy"] = "official-source-id-reconciliation"
        row["sourceIdentity"] = sid
        jobs.append(row)
        recovered.append(sid)

    # De-dupe only exact source identities. Rows without a stable source ID are left untouched;
    # presentation grouping belongs in the UI, not in the evidence ledger.
    seen_ids = set()
    kept = []
    for job in jobs:
        sid = source_id(job)
        if sid:
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
        kept.append(job)
    jobs = kept

    dataset_ids_after = {source_id(j) for j in jobs if source_id(j)}
    missing_after = sorted(official_ids - dataset_ids_after)

    # Append-only evidence ledger: once an official source ID has been observed, never forget it.
    current_official = set()
    office_by_id = {}
    for office in offices:
        for sid in office["officialIds"]:
            current_official.add(sid)
            office_by_id[sid] = (office["province"], office["name"])
    for sid, row in official.items():
        old = entries.get(sid, {})
        province, office = office_by_id.get(sid, (row.get("province", ""), row.get("source", "")))
        entries[sid] = {
            "sourceIdentity": sid,
            "province": province, "source": office,
            "title": row.get("title", ""), "school": row.get("school", ""),
            "registered": row.get("registered", ""), "url": row.get("url", ""),
            "firstSeen": old.get("firstSeen") or NOW_S, "lastSeen": NOW_S,
            "seenCount": int(old.get("seenCount") or 0) + 1,
            "presentInLatestOfficialScan": True,
        }
    for sid, old in entries.items():
        if sid not in current_official:
            old["presentInLatestOfficialScan"] = False

    # Office-level expected-vs-collected evidence.
    total_missing_after = 0
    incomplete_offices = []
    for office in offices:
        ids = set(office.pop("officialIds"))
        before = ids & dataset_ids_before
        after = ids & dataset_ids_after
        office_missing_before = sorted(ids - dataset_ids_before)
        office_missing_after = sorted(ids - dataset_ids_after)
        office["inDatasetBefore"] = len(before)
        office["missingBeforeCount"] = len(office_missing_before)
        office["recoveredNowCount"] = len(set(office_missing_before) & set(recovered))
        office["inDatasetAfter"] = len(after)
        office["missingAfterCount"] = len(office_missing_after)
        office["missingAfterExamples"] = office_missing_after[:20]
        office["reconciled"] = bool(office["coverageComplete"] and not office_missing_after)
        total_missing_after += len(office_missing_after)
        if not office["reconciled"]:
            incomplete_offices.append(office["name"])

    payload["jobs"] = jobs
    payload["sourceReconciliation"] = {
        "generatedAt": NOW_S,
        "lookbackDays": cov.LOOKBACK_DAYS,
        "officialIdCount": len(official_ids),
        "datasetIdCountBefore": len(dataset_ids_before),
        "missingBefore": len(missing_before),
        "recoveredNow": len(recovered),
        "missingAfter": len(missing_after),
        "reconciledOffices": sum(1 for x in offices if x["reconciled"]),
        "totalOffices": len(offices),
    }
    payload["jobs"].sort(key=lambda j: (j.get("registered", ""), j.get("applyEnd", "")), reverse=True)
    JOBS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    ledger = {
        "generatedAt": NOW_S,
        "policy": "append-only stable official posting IDs; title similarity never deletes source evidence",
        "knownOfficialIdCount": len(entries),
        "entries": entries,
    }
    LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "generatedAt": NOW_S,
        "lookbackDays": cov.LOOKBACK_DAYS,
        "summary": payload["sourceReconciliation"],
        "incompleteOffices": incomplete_offices,
        "missingAfterExamples": missing_after[:50],
        "offices": offices,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))

    # Do not certify a scan that did not traverse an office completely, even if its current IDs match.
    if incomplete_offices or total_missing_after:
        raise SystemExit(f"Source-ID reconciliation incomplete: offices={len(incomplete_offices)}, missing={total_missing_after}")


if __name__ == "__main__":
    main()
