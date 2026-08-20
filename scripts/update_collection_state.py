#!/usr/bin/env python3
"""Persist an append-only recruitment ledger and per-source checkpoints.

This does not replace the crawlers. It gives them durable memory: once a posting has been
seen, its canonical identity and first/last-seen timestamps survive later transient crawl gaps.
The checkpoint file records the newest stable identifiers observed per source/board so future
incremental collectors can stop at already-known postings instead of rescanning blindly.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs.json"
LEDGER = ROOT / "job_ledger.json"
STATE = ROOT / "collection_state.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def norm_text(v):
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


def canonical_url(raw):
    if not raw:
        return ""
    try:
        p = urlparse(raw)
        q = parse_qs(p.query, keep_blank_values=True)
        keep = {}
        for key in ("pbancSn", "bbsId", "nttSn", "mi", "job_seq", "seq", "clasHmpgId"):
            if q.get(key):
                keep[key] = q[key][0]
        return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, "", urlencode(keep), ""))
    except Exception:
        return str(raw).strip()


def identity(job):
    params = job.get("openParams") or {}
    seq = str(params.get("job_seq") or "")
    if seq.isdigit():
        return f"seoul:{urlparse(job.get('openUrl') or job.get('url') or '').netloc}:{seq}"
    ntt = str(job.get("nttSn") or "")
    bbs = str(job.get("bbsId") or "")
    if ntt.isdigit() and bbs.isdigit():
        return f"mircms:{urlparse(job.get('url') or job.get('boardUrl') or '').netloc}:{bbs}:{ntt}"
    raw = job.get("url") or ""
    try:
        q = parse_qs(urlparse(raw).query)
        pb = (q.get("pbancSn") or [""])[0]
        if pb.isdigit():
            return f"goe:{pb}"
        seq2 = (q.get("job_seq") or [""])[0]
        if seq2.isdigit():
            return f"seoul:{urlparse(raw).netloc}:{seq2}"
        ntt2 = (q.get("nttSn") or [""])[0]
        bbs2 = (q.get("bbsId") or [""])[0]
        if ntt2.isdigit() and bbs2.isdigit():
            return f"mircms:{urlparse(raw).netloc}:{bbs2}:{ntt2}"
    except Exception:
        pass
    cu = canonical_url(raw)
    if cu:
        return "url:" + cu
    return "fallback:" + "|".join([
        norm_text(job.get("province")), norm_text(job.get("source")),
        norm_text(job.get("school")), norm_text(job.get("title")), norm_text(job.get("registered")),
    ])


def numeric_id(job):
    candidates = []
    for v in (job.get("nttSn"), (job.get("openParams") or {}).get("job_seq")):
        s = str(v or "")
        if s.isdigit(): candidates.append(int(s))
    try:
        q = parse_qs(urlparse(job.get("url") or "").query)
        for k in ("pbancSn", "nttSn", "job_seq", "seq"):
            s = (q.get(k) or [""])[0]
            if s.isdigit(): candidates.append(int(s))
    except Exception:
        pass
    return max(candidates) if candidates else None


def main():
    payload = load(JOBS, {})
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    if len(jobs) < 100:
        raise SystemExit(f"Refusing to update collection memory from suspicious dataset: {len(jobs)} jobs")

    old_ledger = load(LEDGER, {"entries": {}})
    entries = old_ledger.get("entries", {}) if isinstance(old_ledger, dict) else {}
    seen_now = set()
    now_s = NOW.strftime("%Y-%m-%d %H:%M:%S KST")

    checkpoints = {}
    for job in jobs:
        key = identity(job)
        seen_now.add(key)
        old = entries.get(key, {})
        snapshot = {
            "identity": key,
            "province": job.get("province", ""), "source": job.get("source", ""),
            "sourceType": job.get("sourceType", ""), "school": job.get("school", ""),
            "title": job.get("title", ""), "subject": job.get("subject", ""),
            "registered": job.get("registered", ""), "applyEnd": job.get("applyEnd", ""),
            "url": job.get("url", ""), "boardUrl": job.get("boardUrl", ""),
            "firstSeen": old.get("firstSeen") or now_s, "lastSeen": now_s,
            "seenCount": int(old.get("seenCount") or 0) + 1, "presentInLatest": True,
        }
        entries[key] = snapshot

        board = job.get("boardUrl") or job.get("source") or "unknown"
        ck = checkpoints.setdefault(board, {
            "source": job.get("source", ""), "province": job.get("province", ""),
            "boardUrl": job.get("boardUrl", ""), "latestNumericId": None,
            "latestRegistered": "", "observedJobs": 0,
        })
        ck["observedJobs"] += 1
        nid = numeric_id(job)
        if nid is not None and (ck["latestNumericId"] is None or nid > ck["latestNumericId"]):
            ck["latestNumericId"] = nid
        reg = str(job.get("registered") or "")
        if reg and reg > ck["latestRegistered"]:
            ck["latestRegistered"] = reg

    for key, old in list(entries.items()):
        if key not in seen_now:
            old["presentInLatest"] = False

    ledger = {
        "generatedAt": now_s,
        "policy": "append-only identities; latest snapshot updates metadata without forgetting previously seen postings",
        "currentDatasetCount": len(jobs),
        "knownPostingCount": len(entries),
        "entries": entries,
    }
    state = {
        "generatedAt": now_s,
        "datasetUpdatedAt": payload.get("updatedAt"),
        "boards": checkpoints,
        "note": "Durable checkpoints for incremental collection and gap detection; numeric IDs are hints, never sole deletion criteria.",
    }
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"current": len(jobs), "known": len(entries), "boards": len(checkpoints)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
