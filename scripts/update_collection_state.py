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

from checkpoint_inventory import seed_configured_boards

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs.json"
LEDGER = ROOT / "job_ledger.json"
STATE = ROOT / "collection_state.json"
SOURCES = ROOT / "sources.json"
BOARD_CACHE = ROOT / "board_cache.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
DATE_RE = re.compile(r"^(20\d{2})[./-](\d{1,2})[./-](\d{1,2})$")


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def norm_text(v):
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


def host_of(value):
    try:
        host = (urlparse(str(value or "")).hostname or "").lower().strip(".")
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def build_support_host_map(sources):
    owners = {}
    for province_key in ("gyeonggi", "seoul"):
        section = sources.get(province_key, {}) if isinstance(sources, dict) else {}
        for office in section.get("supportOffices", []) or []:
            name = str(office.get("name") or "").strip()
            if not name:
                continue
            urls = [office.get("url"), office.get("boardUrl")]
            urls.extend(office.get("boardUrls") or [])
            for raw in urls:
                host = host_of(raw)
                if host:
                    owners.setdefault(host, set()).add(name)
    return {host: next(iter(names)) for host, names in owners.items() if len(names) == 1}


def canonical_source(raw_source, host_map, *urls):
    old = str(raw_source or "").strip()
    if "교육지원청" not in old:
        return old
    for raw in urls:
        canonical = host_map.get(host_of(raw))
        if canonical:
            return canonical
    return old


def valid_registered(value):
    """Return canonical YYYY/MM/DD only for plausible non-future registration dates."""
    s = str(value or "").strip()
    m = DATE_RE.match(s)
    if not m:
        return ""
    try:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=KST)
    except ValueError:
        return ""
    if dt.date() > (NOW + timedelta(days=1)).date():
        return ""
    return dt.strftime("%Y/%m/%d")


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
        if s.isdigit():
            candidates.append(int(s))
    try:
        q = parse_qs(urlparse(job.get("url") or "").query)
        for k in ("pbancSn", "nttSn", "job_seq", "seq"):
            s = (q.get(k) or [""])[0]
            if s.isdigit():
                candidates.append(int(s))
    except Exception:
        pass
    return max(candidates) if candidates else None


def observed_highwater(checkpoint):
    """Keep the largest previously observed count even after partial/specialized refreshes."""
    values = []
    for key in ("observedJobs", "lastObservedJobs", "maxObservedJobs"):
        try:
            values.append(int(checkpoint.get(key) or 0))
        except (TypeError, ValueError):
            pass
    return max(values) if values else 0


def main():
    payload = load(JOBS, {})
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    if len(jobs) < 100:
        raise SystemExit(f"Refusing to update collection memory from suspicious dataset: {len(jobs)} jobs")

    sources = load(SOURCES, {})
    board_cache = load(BOARD_CACHE, {})
    host_map = build_support_host_map(sources)
    old_ledger = load(LEDGER, {"entries": {}})
    entries = old_ledger.get("entries", {}) if isinstance(old_ledger, dict) else {}
    old_state = load(STATE, {"boards": {}})
    old_checkpoints = old_state.get("boards", {}) if isinstance(old_state, dict) else {}
    seen_now = set()
    now_s = NOW.strftime("%Y-%m-%d %H:%M:%S KST")

    checkpoints = {}
    for job in jobs:
        key = identity(job)
        seen_now.add(key)
        old = entries.get(key, {})
        reg = valid_registered(job.get("registered"))
        source = canonical_source(
            job.get("source", ""), host_map,
            job.get("url"), job.get("openUrl"), job.get("boardUrl"),
        )
        snapshot = {
            "identity": key,
            "province": job.get("province", ""), "source": source,
            "sourceType": job.get("sourceType", ""), "school": job.get("school", ""),
            "title": job.get("title", ""), "subject": job.get("subject", ""),
            "registered": reg, "applyEnd": job.get("applyEnd", ""),
            "url": job.get("url", ""), "boardUrl": job.get("boardUrl", ""),
            "firstSeen": old.get("firstSeen") or now_s, "lastSeen": now_s,
            "seenCount": int(old.get("seenCount") or 0) + 1, "presentInLatest": True,
        }
        entries[key] = snapshot

        board = job.get("boardUrl") or source or "unknown"
        ck = checkpoints.setdefault(board, {
            "source": source, "province": job.get("province", ""),
            "boardUrl": job.get("boardUrl", ""), "latestNumericId": None,
            "latestRegistered": "", "observedJobs": 0, "presentInLatest": True,
        })
        ck["observedJobs"] += 1
        nid = numeric_id(job)
        if nid is not None and (ck["latestNumericId"] is None or nid > ck["latestNumericId"]):
            ck["latestNumericId"] = nid
        if reg and reg > ck["latestRegistered"]:
            ck["latestRegistered"] = reg

    # Checkpoints are durable memory, not a snapshot-only index. Preserve boards that
    # temporarily disappear from jobs.json and never move stable high-water marks backward.
    for board, old_ck in old_checkpoints.items():
        if not isinstance(old_ck, dict):
            continue
        highwater = observed_highwater(old_ck)
        if board not in checkpoints:
            preserved = dict(old_ck)
            preserved["source"] = canonical_source(
                old_ck.get("source", ""), host_map, board, old_ck.get("boardUrl"),
            )
            preserved["presentInLatest"] = False
            preserved["lastObservedJobs"] = highwater
            preserved["maxObservedJobs"] = highwater
            checkpoints[board] = preserved
            continue
        ck = checkpoints[board]
        old_nid = old_ck.get("latestNumericId")
        if isinstance(old_nid, int) and (ck["latestNumericId"] is None or old_nid > ck["latestNumericId"]):
            ck["latestNumericId"] = old_nid
        old_reg = valid_registered(old_ck.get("latestRegistered"))
        if old_reg and old_reg > ck["latestRegistered"]:
            ck["latestRegistered"] = old_reg
        ck["lastObservedJobs"] = highwater
        ck["maxObservedJobs"] = max(highwater, int(ck.get("observedJobs") or 0))

    # Board inventory is source configuration, not a property of one job snapshot. Seed every
    # configured/discovered official board so a partial refresh cannot make the monitor forget it.
    seed_configured_boards(
        checkpoints, sources, board_cache, host_map, canonical_source, observed_highwater,
    )

    for board, ck in checkpoints.items():
        if "maxObservedJobs" not in ck:
            ck["maxObservedJobs"] = int(ck.get("observedJobs") or 0)

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
        "note": "Durable board checkpoints and configured official board inventory survive partial crawl gaps; source attribution follows unambiguous official support-office hosts; numeric IDs and observed-count high-water marks never move backward.",
    }
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"current": len(jobs), "known": len(entries), "boards": len(checkpoints)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
