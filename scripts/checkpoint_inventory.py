#!/usr/bin/env python3
"""Keep collection_state board inventory independent from partial job snapshots.

A specialized/partial refresh can legitimately touch only a subset of boards. The durable
checkpoint file must still remember every configured or discovered official board so a board
cannot silently disappear from monitoring merely because no row from it was present in one
jobs.json snapshot.
"""
from urllib.parse import urlparse

CENTRAL_SOURCE_NAMES = ("경기도교육청 통합 구인구직", "서울교육일자리포털")


def _host(value):
    try:
        host = (urlparse(str(value or "")).hostname or "").lower().strip(".")
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _configured_boards(sources, cache):
    seen = set()
    for province_key, province_label in (("gyeonggi", "경기"), ("seoul", "서울")):
        section = sources.get(province_key, {}) if isinstance(sources, dict) else {}
        for office in section.get("supportOffices", []) or []:
            name = str(office.get("name") or "").strip()
            urls = []
            urls.extend(office.get("boardUrls") or [])
            if office.get("boardUrl"):
                urls.append(office.get("boardUrl"))
            if isinstance(cache, dict):
                urls.extend(cache.get(name, []) or [])
            for raw in urls:
                board = str(raw or "").strip()
                if not board or board in seen:
                    continue
                seen.add(board)
                yield board, name, province_label


def _drop_synthetic_source_checkpoints(checkpoints):
    """Remove state-only pseudo sources created by cross-source duplicate attribution.

    A checkpoint with no board URL such as
    '경기도교육청 통합 구인구직 + 성남교육지원청' is not a real source. Keeping it distorts
    per-source high-water marks and missing-source monitoring. Real official board checkpoints
    and the central source checkpoints are preserved.
    """
    for key, ck in list(checkpoints.items()):
        if not isinstance(ck, dict):
            continue
        source = str(ck.get("source") or key or "").strip()
        board_url = str(ck.get("boardUrl") or "").strip()
        mixed = "+" in source and "교육지원청" in source and any(x in source for x in CENTRAL_SOURCE_NAMES)
        if mixed and not board_url:
            checkpoints.pop(key, None)


def seed_configured_boards(checkpoints, sources, cache, host_map, canonical_source, observed_highwater):
    """Ensure every known official board has a durable checkpoint entry.

    Existing observations always win. Newly seeded entries have no invented numeric ID or count;
    they simply preserve board inventory and make absence explicit with presentInLatest=False.
    """
    _drop_synthetic_source_checkpoints(checkpoints)

    for board, name, province in _configured_boards(sources, cache):
        if board in checkpoints:
            ck = checkpoints[board]
            ck.setdefault("configuredOfficialBoard", True)
            continue
        canonical = canonical_source(name, host_map, board) or name
        checkpoints[board] = {
            "source": canonical,
            "province": province,
            "boardUrl": board,
            "latestNumericId": None,
            "latestRegistered": "",
            "observedJobs": 0,
            "lastObservedJobs": 0,
            "maxObservedJobs": 0,
            "presentInLatest": False,
            "configuredOfficialBoard": True,
        }
    return checkpoints
