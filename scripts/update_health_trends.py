#!/usr/bin/env python3
"""Build rolling 1d/7d/30d health evidence for 수도권에듀잡.

This observer never mutates canonical recruitment data. It snapshots official, Lessoninfo and
unified-search health, compares the current state with prior baselines, and emits warnings for
sudden drops or source-level stalls. Hard integrity failures remain fail-closed signals.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
HISTORY = Path("health_trend_history.json")
REPORT = Path("health_anomaly_report.json")
MAX_SNAPSHOTS = 800
DATE_RE = re.compile(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})")


def now_kst() -> datetime:
    return datetime.now(KST)


def load(path: str, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def jobs_of(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return [x for x in (data.get("jobs") or []) if isinstance(x, dict)]
    return []


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except ValueError:
        return None


def date_key(value: Any) -> str:
    m = DATE_RE.search(str(value or ""))
    if not m:
        return ""
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
    except ValueError:
        return ""


def official_source_stats(jobs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    latest: dict[str, str] = {}
    for j in jobs:
        source = str(j.get("source") or "미확인").strip()
        counts[source] += 1
        d = date_key(j.get("registered"))
        if d and d > latest.get(source, ""):
            latest[source] = d
    return {k: {"count": counts[k], "latestRegistered": latest.get(k, "")} for k in sorted(counts)}


def nearest_prior(snaps: list[dict[str, Any]], now: datetime, hours: int) -> dict[str, Any] | None:
    target = now - timedelta(hours=hours)
    candidates = []
    for s in snaps:
        dt = parse_dt(s.get("generatedAt"))
        if dt and dt <= now:
            candidates.append((abs((dt - target).total_seconds()), dt, s))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    # Avoid pretending a very recent snapshot is a 30-day baseline.
    chosen = candidates[0]
    tolerance = max(6, hours * 0.35) * 3600
    return chosen[2] if chosen[0] <= tolerance else None


def ratio_change(cur: int | float | None, prev: int | float | None) -> float | None:
    if not isinstance(cur, (int, float)) or not isinstance(prev, (int, float)) or prev == 0:
        return None
    return round((cur - prev) / prev, 4)


def metric(snapshot: dict[str, Any], group: str, key: str) -> Any:
    g = snapshot.get(group) or {}
    return g.get(key) if isinstance(g, dict) else None


def comparison(cur: dict[str, Any], prev: dict[str, Any] | None) -> dict[str, Any] | None:
    if not prev:
        return None
    pairs = [
        ("official", "datasetCount"),
        ("official", "officialIdCount"),
        ("lessoninfo", "publishedIdCount"),
        ("unified", "publishedJobs"),
        ("unified", "officialDisplayed"),
        ("unified", "privateDiscovered"),
    ]
    out = {"baselineAt": prev.get("generatedAt", ""), "metrics": {}}
    for group, key in pairs:
        c, p = metric(cur, group, key), metric(prev, group, key)
        out["metrics"][f"{group}.{key}"] = {"current": c, "baseline": p, "changeRatio": ratio_change(c, p)}
    return out


def main() -> int:
    now = now_kst()
    official_data = load("jobs.json", {})
    official_jobs = jobs_of(official_data)
    rec = load("source_reconciliation_report.json", {})
    rec_summary = rec.get("summary", {}) if isinstance(rec, dict) else {}
    lesson = load("lessoninfo_reconciliation_report.json", {})
    lesson_jobs = jobs_of(load("lessoninfo_jobs.json", {}))
    unified = load("unified_search_report.json", {})
    validation = load("unified_validation_report.json", {})

    snapshot = {
        "generatedAt": now.isoformat(timespec="seconds"),
        "official": {
            "datasetCount": len(official_jobs),
            "officialIdCount": int(rec_summary.get("officialIdCount") or 0),
            "missingAfter": int(rec_summary.get("missingAfter") or 0),
            "reconciledSources": int(rec_summary.get("reconciledSources") or 0),
            "totalSources": int(rec_summary.get("totalSources") or 0),
        },
        "lessoninfo": {
            "datasetCount": len(lesson_jobs),
            "publishedIdCount": int(lesson.get("publishedIdCount") or lesson.get("activeIdCount") or 0),
            "missingAfter": int(lesson.get("missingAfterCount") or 0),
            "detailErrors": int(lesson.get("detailErrorCount") or 0),
            "healthy": lesson.get("healthy") is True,
            "traversalComplete": lesson.get("traversalComplete") is True,
            "perSurface": lesson.get("perSurfaceActive") or {},
        },
        "unified": {
            "publishedJobs": int(unified.get("publishedJobs") or validation.get("total") or 0),
            "officialDisplayed": int((unified.get("perFeed") or {}).get("official") or validation.get("official") or 0),
            "privateDisplayed": int((unified.get("perFeed") or {}).get("private") or validation.get("privateDisplayed") or 0),
            "privateDiscovered": int(unified.get("selectedPrivateJobs") or validation.get("lessoninfoStableIds") or 0),
            "officialPrivateAliases": int(unified.get("explicitOfficialAliasGroupsMerged") or validation.get("lessoninfoRepresentedByOfficial") or 0),
            "healthy": validation.get("healthy") is True,
            "missingLessoninfoIds": int(validation.get("missingLessoninfoIds") or 0),
            "nonMetroPrivate": int(validation.get("nonMetroPrivate") or 0),
            "bannedPrivate": int(validation.get("bannedPrivate") or 0),
            "expiredPrivate": int(validation.get("expiredPrivate") or 0),
            "futurePrivateDates": int(validation.get("futurePrivateDates") or 0),
            "duplicateStableIds": int(validation.get("duplicateStableIds") or 0),
            "missingLinks": int(validation.get("missingLinks") or 0),
        },
        "officialSources": official_source_stats(official_jobs),
    }

    hist = load(str(HISTORY), {"version": 1, "snapshots": []})
    snaps = hist.get("snapshots", []) if isinstance(hist, dict) else []
    snaps = [x for x in snaps if isinstance(x, dict)]
    baselines = {label: nearest_prior(snaps, now, hours) for label, hours in (("1d", 24), ("7d", 168), ("30d", 720))}
    comparisons = {k: comparison(snapshot, v) for k, v in baselines.items()}

    anomalies: list[dict[str, Any]] = []
    def add(severity: str, code: str, message: str, **evidence: Any) -> None:
        anomalies.append({"severity": severity, "code": code, "message": message, "evidence": evidence})

    o = snapshot["official"]
    if o["missingAfter"] > 0 or (o["totalSources"] and o["reconciledSources"] < o["totalSources"]):
        add("critical", "official-reconciliation-failed", "공식 출처 ID 대조가 완전성 기준을 통과하지 못했습니다.", **o)

    li = snapshot["lessoninfo"]
    if not li["healthy"] or not li["traversalComplete"] or li["missingAfter"] > 0 or li["detailErrors"] > 0:
        add("critical", "lessoninfo-integrity-failed", "레슨인포 수집/ID 대조/상세검증 중 하나 이상이 실패했습니다.", **li)

    u = snapshot["unified"]
    hard_unified = ["missingLessoninfoIds", "nonMetroPrivate", "bannedPrivate", "expiredPrivate", "futurePrivateDates", "duplicateStableIds", "missingLinks"]
    if not u["healthy"] or any(int(u[k]) > 0 for k in hard_unified):
        add("critical", "unified-search-integrity-failed", "통합검색 검증이 정상 기준을 통과하지 못했습니다.", **u)

    one = baselines.get("1d")
    if one:
        for group, key, threshold, min_prev, code in (
            ("official", "datasetCount", -0.35, 500, "official-dataset-sudden-drop"),
            ("lessoninfo", "publishedIdCount", -0.50, 20, "lessoninfo-sudden-drop"),
            ("unified", "publishedJobs", -0.40, 500, "unified-sudden-drop"),
        ):
            cur, prev = metric(snapshot, group, key), metric(one, group, key)
            ch = ratio_change(cur, prev)
            if isinstance(prev, (int, float)) and prev >= min_prev and ch is not None and ch <= threshold:
                add("warning", code, f"{group}.{key}가 24시간 기준으로 비정상 급감했습니다.", current=cur, baseline=prev, changeRatio=ch)

        prior_sources = one.get("officialSources") or {}
        current_sources = snapshot["officialSources"]
        for name, old in prior_sources.items():
            if not isinstance(old, dict):
                continue
            before = int(old.get("count") or 0)
            after = int((current_sources.get(name) or {}).get("count") or 0)
            if before >= 5 and after == 0:
                add("warning", "official-source-zero-drop", "기존에 공고가 있던 공식 출처가 갑자기 0건이 되었습니다.", source=name, baseline=before, current=after)

    seven = baselines.get("7d")
    if seven:
        prior_sources = seven.get("officialSources") or {}
        current_sources = snapshot["officialSources"]
        newest_dates = [v.get("latestRegistered", "") for v in current_sources.values() if isinstance(v, dict) and v.get("latestRegistered")]
        global_latest = max(newest_dates) if newest_dates else ""
        if global_latest:
            for name, curinfo in current_sources.items():
                oldinfo = prior_sources.get(name) or {}
                latest = curinfo.get("latestRegistered", "")
                old_latest = oldinfo.get("latestRegistered", "") if isinstance(oldinfo, dict) else ""
                if latest and latest == old_latest and latest < global_latest and int(curinfo.get("count") or 0) > 0:
                    try:
                        age = (datetime.fromisoformat(global_latest).date() - datetime.fromisoformat(latest).date()).days
                    except ValueError:
                        age = 0
                    if age >= 7:
                        add("warning", "official-source-freshness-stall", "다른 출처는 갱신되는데 특정 공식 출처의 최신 등록일이 7일 이상 정체되어 있습니다.", source=name, latestRegistered=latest, globalLatest=global_latest, ageDays=age)

    critical = [a for a in anomalies if a["severity"] == "critical"]
    warnings = [a for a in anomalies if a["severity"] == "warning"]
    status = "critical" if critical else ("degraded" if warnings else "healthy")
    report = {
        "generatedAt": snapshot["generatedAt"],
        "status": status,
        "healthy": not critical,
        "snapshot": snapshot,
        "comparisons": comparisons,
        "anomalies": anomalies,
        "criticalCount": len(critical),
        "warningCount": len(warnings),
        "policy": "hard integrity failures are critical; trend drops/stalls are warnings until independently verified",
    }

    snaps.append(snapshot)
    hist = {"version": 1, "updatedAt": snapshot["generatedAt"], "snapshots": snaps[-MAX_SNAPSHOTS:]}
    HISTORY.write_text(json.dumps(hist, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "critical": len(critical), "warnings": len(warnings)}, ensure_ascii=False))
    return 2 if critical else 0


if __name__ == "__main__":
    raise SystemExit(main())
