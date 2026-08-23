#!/usr/bin/env python3
"""Independent stable-ID reconciliation for all 38 official recruitment sources.

Support offices are re-traversed through the completeness crawler and central portals are
re-read through their source-native IDs.  The source ID, not title similarity, is the evidence
unit: Gyeonggi support bbsId+nttSn, Seoul support host+job_seq, Gyeonggi central pbancSn,
and Seoul central q_rcrtSn. Missing source occurrences are restored before publication.
"""
import json
from datetime import timedelta, timezone, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import complete_support_coverage as cov
import scrape_jobs as primary

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
    raw = job.get("url") or ""
    try:
        q = parse_qs(urlparse(raw).query)
    except Exception:
        q = {}

    if job.get("sourceType") == "통합게시판":
        pb = str((q.get("pbancSn") or [""])[0])
        if pb.isdigit():
            return f"goe-central:{pb}"
        rid = str((q.get("q_rcrtSn") or [""])[0])
        if rid.isdigit():
            return f"seoul-central:{rid}"

    province = str(job.get("province") or "")
    if province == "서울":
        seq = str((job.get("openParams") or {}).get("job_seq") or "")
        host = (urlparse(job.get("openUrl") or raw).hostname or "").lower()
        if not seq.isdigit():
            seq = str((q.get("job_seq") or [""])[0])
        return f"seoul:{host}:{seq}" if host and seq.isdigit() else ""

    if province == "경기":
        try:
            u = urlparse(raw)
            bbs = str(job.get("bbsId") or (q.get("bbsId") or [""])[0])
            ntt = str(job.get("nttSn") or (q.get("nttSn") or [""])[0])
            host = (u.hostname or "").lower()
            return f"mircms:{host}:{bbs}:{ntt}" if host and bbs.isdigit() and ntt.isdigit() else ""
        except Exception:
            return ""
    return ""


def source_status(province, name, rows, coverage_complete=True, pages_scanned=0, access_errors=0, boards=None):
    ids = sorted({source_id(x) for x in rows if source_id(x)})
    return {
        "province": province, "name": name, "boards": boards or [],
        "officialIds": ids, "officialIdCount": len(ids),
        "coverageComplete": bool(coverage_complete), "pagesScanned": int(pages_scanned or 0),
        "accessErrors": int(access_errors or 0),
    }


def crawl_official_ids():
    sources = []
    all_rows = []

    # Central portals: native source IDs are retained in their detail URLs.
    gg_central = primary.scrape_gyeonggi_central()
    sources.append(source_status("경기", primary.GYEONGGI["central"]["name"], gg_central,
                                 coverage_complete=bool(gg_central), boards=[primary.GYEONGGI["central"]["url"]]))
    all_rows.extend(gg_central)

    se_central = primary.scrape_seoul_central()
    sources.append(source_status("서울", primary.SEOUL["central"]["name"], se_central,
                                 coverage_complete=bool(se_central), boards=[primary.SEOUL["central"]["url"]]))
    all_rows.extend(se_central)

    # 25 Gyeonggi support offices: every configured/cached MirCMS recruitment board.
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
        sources.append(source_status(
            "경기", src["name"], rows,
            coverage_complete=bool(metas) and all(m.get("coverageComplete") for m in metas),
            pages_scanned=sum(int(m.get("pagesScanned") or 0) for m in metas),
            access_errors=sum(1 for m in metas if m.get("accessError")), boards=boards,
        ))
        all_rows.extend(rows)

    # 11 Seoul support offices: native job_seq is the source identity.
    for src in cov.SOURCES["seoul"]["supportOffices"]:
        rows, meta = cov.seoul_board(src)
        sources.append(source_status(
            "서울", src["name"], rows, coverage_complete=bool(meta.get("coverageComplete")),
            pages_scanned=int(meta.get("pagesScanned") or 0),
            access_errors=1 if meta.get("accessError") else 0,
            boards=[src.get("boardUrl", "")],
        ))
        all_rows.extend(rows)

    # Menu aliases and multi-category listings may expose the same ID repeatedly.
    by_id = {}
    for row in all_rows:
        sid = source_id(row)
        if sid and sid not in by_id:
            by_id[sid] = row
    return sources, by_id


def main():
    payload = load(JOBS_PATH, {})
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    if len(jobs) < 100:
        raise SystemExit(f"Refusing ID reconciliation against suspicious dataset: {len(jobs)} jobs")

    old_ledger = load(LEDGER_PATH, {"entries": {}})
    entries = old_ledger.get("entries", {}) if isinstance(old_ledger, dict) else {}

    sources, official = crawl_official_ids()
    dataset_ids_before = {source_id(j) for j in jobs if source_id(j)}
    official_ids = set(official)
    missing_before = sorted(official_ids - dataset_ids_before)

    # Exact-ID recovery. Similar titles never suppress a distinct official source occurrence.
    recovered = []
    for sid in missing_before:
        row = dict(official[sid])
        row["recoveredBy"] = "official-source-id-reconciliation"
        row["sourceIdentity"] = sid
        jobs.append(row)
        recovered.append(sid)

    # De-dupe only exact stable source IDs; leave non-ID records to later conservative merge/UI grouping.
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

    # Append-only source evidence ledger.
    current_official = set()
    source_by_id = {}
    for src in sources:
        for sid in src["officialIds"]:
            current_official.add(sid)
            source_by_id[sid] = (src["province"], src["name"])
    for sid, row in official.items():
        old = entries.get(sid, {})
        province, source = source_by_id.get(sid, (row.get("province", ""), row.get("source", "")))
        entries[sid] = {
            "sourceIdentity": sid, "province": province, "source": source,
            "title": row.get("title", ""), "school": row.get("school", ""),
            "registered": row.get("registered", ""), "url": row.get("url", ""),
            "firstSeen": old.get("firstSeen") or NOW_S, "lastSeen": NOW_S,
            "seenCount": int(old.get("seenCount") or 0) + 1,
            "presentInLatestOfficialScan": True,
        }
    for sid, old in entries.items():
        if sid not in current_official:
            old["presentInLatestOfficialScan"] = False

    total_missing_after = 0
    incomplete_sources = []
    for src in sources:
        ids = set(src.pop("officialIds"))
        missing_src_before = sorted(ids - dataset_ids_before)
        missing_src_after = sorted(ids - dataset_ids_after)
        src["inDatasetBefore"] = len(ids & dataset_ids_before)
        src["missingBeforeCount"] = len(missing_src_before)
        src["recoveredNowCount"] = len(set(missing_src_before) & set(recovered))
        src["inDatasetAfter"] = len(ids & dataset_ids_after)
        src["missingAfterCount"] = len(missing_src_after)
        src["missingAfterExamples"] = missing_src_after[:20]
        src["reconciled"] = bool(src["coverageComplete"] and not missing_src_after)
        total_missing_after += len(missing_src_after)
        if not src["reconciled"]:
            incomplete_sources.append(src["name"])

    payload["jobs"] = jobs
    payload["sourceReconciliation"] = {
        "generatedAt": NOW_S, "lookbackDays": cov.LOOKBACK_DAYS,
        "officialIdCount": len(official_ids), "datasetIdCountBefore": len(dataset_ids_before),
        "missingBefore": len(missing_before), "recoveredNow": len(recovered),
        "missingAfter": len(missing_after),
        "reconciledSources": sum(1 for x in sources if x["reconciled"]),
        "totalSources": len(sources),
        # Backward-compatible aliases used by existing guards/monitoring.
        "reconciledOffices": sum(1 for x in sources if x["reconciled"]),
        "totalOffices": len(sources),
    }
    payload["jobs"].sort(key=lambda j: (j.get("registered", ""), j.get("applyEnd", "")), reverse=True)
    JOBS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    LEDGER_PATH.write_text(json.dumps({
        "generatedAt": NOW_S,
        "policy": "append-only stable official posting IDs across all 38 sources; title similarity never deletes source evidence",
        "knownOfficialIdCount": len(entries), "entries": entries,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "generatedAt": NOW_S, "lookbackDays": cov.LOOKBACK_DAYS,
        "summary": payload["sourceReconciliation"],
        "incompleteSources": incomplete_sources,
        "incompleteOffices": incomplete_sources,
        "missingAfterExamples": missing_after[:50], "sources": sources, "offices": sources,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))

    if incomplete_sources or total_missing_after:
        raise SystemExit(f"Source-ID reconciliation incomplete: sources={len(incomplete_sources)}, missing={total_missing_after}")


if __name__ == "__main__":
    main()
