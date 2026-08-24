#!/usr/bin/env python3
"""Restore official posting IDs that disappeared from an incremental fast crawl.

This is intentionally narrower than a full 38-source reconciliation. It compares the current
jobs.json against source_id_ledger.json, groups only missing IDs by their official source, and
re-crawls those sources. If every ledger-protected ID can be restored, publication may continue;
otherwise the fast workflow fails and keeps the previous published dataset.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import complete_support_coverage as cov
import reconcile_source_ids as rec
import scrape_jobs as primary

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs.json"
LEDGER = ROOT / "source_id_ledger.json"
REPORT = ROOT / "official_id_restore_report.json"
KST = timezone(timedelta(hours=9))


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def protected_entries(ledger):
    entries = ledger.get("entries", {}) if isinstance(ledger, dict) else {}
    return {
        sid: entry for sid, entry in entries.items()
        if isinstance(entry, dict) and entry.get("presentInLatestOfficialScan") is True
    }


def unique_by_id(rows):
    out = {}
    for row in rows:
        sid = rec.source_id(row)
        if sid and sid not in out:
            out[sid] = row
    return out


def crawl_target(province, source):
    if source == primary.GYEONGGI["central"]["name"]:
        return primary.scrape_gyeonggi_central(), {"kind": "central", "source": source}
    if source == primary.SEOUL["central"]["name"]:
        return primary.scrape_seoul_central(), {"kind": "central", "source": source}

    if province == "경기":
        src = next((x for x in cov.SOURCES["gyeonggi"]["supportOffices"] if x.get("name") == source), None)
        if not src:
            return [], {"kind": "support", "source": source, "error": "source-config-not-found"}
        boards = []
        for u in list(src.get("boardUrls", [])) + list(cov.CACHE.get(src["name"], [])):
            if u and u not in boards:
                boards.append(u)
        rows = []
        metas = []
        rows_by_board = {}
        for board in boards:
            found, meta = cov.gyeonggi_board(board, src)
            rows.extend(found)
            metas.append(meta)
            rows_by_board[meta.get("url", board)] = found
        effective = rec.effective_gyeonggi_metas(metas, rows_by_board)
        ok = bool(effective) and all(m.get("coverageComplete") for m in effective)
        return rows, {
            "kind": "support", "source": source, "boards": len(boards),
            "coverageComplete": ok, "accessErrors": sum(1 for m in effective if m.get("accessError")),
        }

    if province == "서울":
        src = next((x for x in cov.SOURCES["seoul"]["supportOffices"] if x.get("name") == source), None)
        if not src:
            return [], {"kind": "support", "source": source, "error": "source-config-not-found"}
        rows, meta = cov.seoul_board(src)
        return rows, {
            "kind": "support", "source": source,
            "coverageComplete": bool(meta.get("coverageComplete")),
            "accessErrors": 1 if meta.get("accessError") else 0,
        }

    return [], {"source": source, "error": "unsupported-province"}


def main():
    payload = load(JOBS, {})
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    ledger = load(LEDGER, {"entries": {}})
    protected = protected_entries(ledger)
    current_ids = {rec.source_id(j) for j in jobs if rec.source_id(j)}
    missing = sorted(set(protected) - current_ids)

    grouped = defaultdict(set)
    for sid in missing:
        entry = protected[sid]
        grouped[(str(entry.get("province") or ""), str(entry.get("source") or ""))].add(sid)

    candidates = {}
    source_results = []
    for (province, source), wanted in sorted(grouped.items()):
        rows, meta = crawl_target(province, source)
        found = unique_by_id(rows)
        recovered_here = sorted(wanted & set(found))
        for sid in recovered_here:
            candidates[sid] = found[sid]
        source_results.append({
            **meta, "province": province, "wanted": len(wanted),
            "recovered": len(recovered_here), "unresolved": len(wanted - set(found)),
            "unresolvedExamples": sorted(wanted - set(found))[:20],
        })

    seen = set(current_ids)
    recovered = []
    for sid in missing:
        row = candidates.get(sid)
        if not row or sid in seen:
            continue
        copy = dict(row)
        copy["recoveredBy"] = "targeted-official-id-restore"
        copy["sourceIdentity"] = sid
        copy["reconciliationCarryForward"] = True
        jobs.append(copy)
        seen.add(sid)
        recovered.append(sid)

    unresolved = sorted(set(protected) - seen)
    jobs.sort(key=lambda j: (j.get("registered", ""), j.get("applyEnd", "")), reverse=True)
    payload["jobs"] = jobs
    payload["officialIdRestore"] = {
        "checkedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "protectedOfficialIds": len(protected),
        "missingBefore": len(missing),
        "recoveredNow": len(recovered),
        "missingAfter": len(unresolved),
    }
    JOBS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "summary": payload["officialIdRestore"],
        "missingBeforeExamples": missing[:50],
        "missingAfterExamples": unresolved[:50],
        "sources": source_results,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))

    if unresolved:
        raise SystemExit(f"Targeted official-ID restore incomplete: {len(unresolved)} IDs remain")


if __name__ == "__main__":
    main()
