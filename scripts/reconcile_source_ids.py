#!/usr/bin/env python3
"""Independent stable-ID reconciliation for all 38 official recruitment sources.

Support offices are re-traversed through the completeness crawler and central portals are
re-read through their source-native IDs. The source ID, not title similarity, is the evidence
unit: Gyeonggi support bbsId+nttSn, Seoul support host+job_seq, Gyeonggi central pbancSn,
and Seoul central q_rcrtSn. Missing source occurrences are restored before publication.

MirCMS can expose one physical board through several menu ids (mi). A menu alias is ignored only
when a completely traversed representative of the same physical board proves that every stable ID
seen through the alias is already contained in the representative, and the alias itself had no
access/pagination error. This covers both zero-row stale menus and partial duplicate menu views
without hiding a genuinely distinct posting.
"""
import json
from datetime import timedelta, timezone, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import complete_support_coverage as cov
import scrape_jobs as primary
import crawl_gyeonggi_central_recent as central_recent

ROOT = Path(__file__).resolve().parents[1]
JOBS_PATH = ROOT / "jobs.json"
LEDGER_PATH = ROOT / "source_id_ledger.json"
REPORT_PATH = ROOT / "source_reconciliation_report.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
NOW_S = NOW.strftime("%Y-%m-%d %H:%M:%S KST")
RECONCILIATION_POLICY = "stable-id-38-v2-gyeonggi-central-90d"


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def is_recruitment_row(job):
    """Keep the reconciliation population aligned with the published recruitment policy."""
    title = str((job or {}).get("title") or "")
    return not bool(primary.EXCLUDE_WORDS.search(title))


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


def physical_board_identity(url):
    """Stable MirCMS board identity, ignoring menu-only `mi` but retaining real filters."""
    try:
        p = urlparse(url or "")
        q = parse_qs(p.query, keep_blank_values=True)
        bbs = (q.get("bbsId") or [""])[0]
        if not bbs:
            return url or ""
        filters = []
        for key in ("srch_aditCol1", "clasHmpgId"):
            if q.get(key):
                filters.append((key, q[key][0]))
        return (p.scheme.lower(), p.netloc.lower(), p.path, bbs, tuple(filters))
    except Exception:
        return url or ""


def ids_for_rows(rows):
    return {sid for sid in (source_id(row) for row in rows if is_recruitment_row(row)) if sid}


def effective_gyeonggi_metas(metas, rows_by_board):
    """Ignore only ID-proven duplicate menu aliases; never hide unique official IDs."""
    groups = {}
    for meta in metas:
        groups.setdefault(physical_board_identity(meta.get("url", "")), []).append(meta)

    for group in groups.values():
        if len(group) < 2:
            continue
        complete = [m for m in group if m.get("coverageComplete")]
        if not complete:
            continue
        representative = max(
            complete,
            key=lambda m: (
                len(ids_for_rows(rows_by_board.get(m.get("url", ""), []))),
                int(m.get("rawRows") or 0),
                int(m.get("pagesScanned") or 0),
            ),
        )
        representative_ids = ids_for_rows(rows_by_board.get(representative.get("url", ""), []))
        for alias in group:
            if alias is representative or alias.get("coverageComplete"):
                continue
            if alias.get("accessError") or alias.get("paginationRepeated"):
                continue
            alias_ids = ids_for_rows(rows_by_board.get(alias.get("url", ""), []))
            if not alias_ids.issubset(representative_ids):
                continue
            alias["ignoredAsDuplicateMenuAlias"] = True
            alias["aliasOf"] = representative.get("url", "")
            alias["aliasStableIdCount"] = len(alias_ids)
            alias["representativeStableIdCount"] = len(representative_ids)

    return [m for m in metas if not m.get("ignoredAsDuplicateMenuAlias")]


def source_status(province, name, rows, coverage_complete=True, pages_scanned=0, access_errors=0, boards=None, board_health=None):
    rows = [x for x in rows if is_recruitment_row(x)]
    ids = sorted({source_id(x) for x in rows if source_id(x)})
    return {
        "province": province, "name": name, "boards": boards or [],
        "officialIds": ids, "officialIdCount": len(ids),
        "coverageComplete": bool(coverage_complete), "pagesScanned": int(pages_scanned or 0),
        "accessErrors": int(access_errors or 0), "boardHealth": board_health or [],
    }


def crawl_official_ids():
    sources = []
    all_rows = []

    gg_central = central_recent.scrape_gyeonggi_central_recent()
    gg_central_health = [dict(x) for x in central_recent.scrape_gyeonggi_central_recent.last_coverage]
    gg_central_complete = (
        bool(gg_central_health)
        and all(x.get("coverageComplete") for x in gg_central_health)
        and not any(x.get("accessError") or x.get("paginationRepeated") for x in gg_central_health)
    )
    sources.append(source_status(
        "경기", primary.GYEONGGI["central"]["name"], gg_central,
        coverage_complete=gg_central_complete,
        pages_scanned=sum(int(x.get("pagesScanned") or 0) for x in gg_central_health),
        access_errors=sum(1 for x in gg_central_health if x.get("accessError")),
        boards=[primary.GYEONGGI["central"]["url"]], board_health=gg_central_health,
    ))
    all_rows.extend(gg_central)

    se_central = primary.scrape_seoul_central()
    sources.append(source_status(
        "서울", primary.SEOUL["central"]["name"], se_central,
        coverage_complete=bool(se_central), boards=[primary.SEOUL["central"]["url"]],
    ))
    all_rows.extend(se_central)

    for src in cov.SOURCES["gyeonggi"]["supportOffices"]:
        boards = []
        for u in list(src.get("boardUrls", [])) + list(cov.CACHE.get(src["name"], [])):
            if u and u not in boards:
                boards.append(u)
        rows = []
        metas = []
        rows_by_board = {}
        for board in boards:
            found, meta = cov.gyeonggi_board(board, src)
            rows_by_board[meta.get("url", board)] = found
            rows.extend(found)
            metas.append(meta)
        effective = effective_gyeonggi_metas(metas, rows_by_board)
        sources.append(source_status(
            "경기", src["name"], rows,
            coverage_complete=bool(effective) and all(m.get("coverageComplete") for m in effective),
            pages_scanned=sum(int(m.get("pagesScanned") or 0) for m in effective),
            access_errors=sum(1 for m in effective if m.get("accessError")),
            boards=boards, board_health=metas,
        ))
        all_rows.extend(rows)

    for src in cov.SOURCES["seoul"]["supportOffices"]:
        rows, meta = cov.seoul_board(src)
        sources.append(source_status(
            "서울", src["name"], rows,
            coverage_complete=bool(meta.get("coverageComplete")),
            pages_scanned=int(meta.get("pagesScanned") or 0),
            access_errors=1 if meta.get("accessError") else 0,
            boards=[src.get("boardUrl", "")], board_health=[meta],
        ))
        all_rows.extend(rows)

    by_id = {}
    for row in all_rows:
        if not is_recruitment_row(row):
            continue
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
    dataset_ids_before = {source_id(j) for j in jobs if source_id(j) and is_recruitment_row(j)}
    official_ids = set(official)
    missing_before = sorted(official_ids - dataset_ids_before)

    recovered = []
    for sid in missing_before:
        row = dict(official[sid])
        row["recoveredBy"] = "official-source-id-reconciliation"
        row["sourceIdentity"] = sid
        jobs.append(row)
        recovered.append(sid)

    seen_ids = set()
    kept = []
    for job in jobs:
        if not is_recruitment_row(job):
            continue
        sid = source_id(job)
        if sid:
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
        kept.append(job)
    jobs = kept

    dataset_ids_after = {source_id(j) for j in jobs if source_id(j)}
    missing_after = sorted(official_ids - dataset_ids_after)

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
        "populationPolicy": RECONCILIATION_POLICY,
        "officialIdCount": len(official_ids),
        "datasetIdCountBefore": len(dataset_ids_before),
        "missingBefore": len(missing_before), "recoveredNow": len(recovered),
        "missingAfter": len(missing_after),
        "reconciledSources": sum(1 for x in sources if x["reconciled"]),
        "totalSources": len(sources),
        "reconciledOffices": sum(1 for x in sources if x["reconciled"]),
        "totalOffices": len(sources),
    }
    payload["jobs"].sort(key=lambda j: (j.get("registered", ""), j.get("applyEnd", "")), reverse=True)
    JOBS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    LEDGER_PATH.write_text(json.dumps({
        "generatedAt": NOW_S,
        "populationPolicy": RECONCILIATION_POLICY,
        "policy": "append-only stable official recruitment posting IDs across all 38 sources; result/selection notices are excluded by title policy; title similarity never deletes source evidence",
        "officialIdCount": len(current_official),
        "knownOfficialIdCount": len(entries), "entries": entries,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "generatedAt": NOW_S, "lookbackDays": cov.LOOKBACK_DAYS,
        "populationPolicy": RECONCILIATION_POLICY,
        "summary": payload["sourceReconciliation"],
        "incompleteSources": incomplete_sources,
        "incompleteOffices": incomplete_sources,
        "missingAfterExamples": missing_after[:50],
        "sources": sources, "offices": sources,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))

    if incomplete_sources or total_missing_after:
        raise SystemExit(f"Source-ID reconciliation incomplete: sources={len(incomplete_sources)}, missing={total_missing_after}")


if __name__ == "__main__":
    main()
