#!/usr/bin/env python3
"""Track exact registry-defined official sources over 1d/7d/30d windows.

Counts come from the stable-ID reconciliation report, not display attribution labels.
Registration freshness is derived from jobs.json, with composite source labels credited to
each exact registry source they explicitly contain. This observer never mutates jobs data.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from source_registry import load_official_registry, official_source_count

KST = timezone(timedelta(hours=9))
HISTORY = Path("official_source_trend_history.json")
REPORT = Path("official_source_trend_report.json")
MAX_SNAPSHOTS = 1000
DATE_RE = re.compile(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})")


def load(path: str, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
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


def registry_names() -> list[str]:
    names: list[str] = []
    for group in load_official_registry().values():
        central = group.get("central") or {}
        if central.get("name"):
            names.append(str(central["name"]))
        for office in group.get("supportOffices") or []:
            if isinstance(office, dict) and office.get("name"):
                names.append(str(office["name"]))
    if len(names) != official_source_count() or len(set(names)) != len(names):
        raise ValueError("official registry names are missing or duplicated")
    return names


def jobs_of(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return [x for x in (data.get("jobs") or []) if isinstance(x, dict)]
    return []


def nearest_prior(snaps: list[dict[str, Any]], now: datetime, hours: int) -> dict[str, Any] | None:
    target = now - timedelta(hours=hours)
    choices: list[tuple[float, dict[str, Any]]] = []
    for snap in snaps:
        dt = parse_dt(snap.get("generatedAt"))
        if dt and dt <= now:
            choices.append((abs((dt - target).total_seconds()), snap))
    if not choices:
        return None
    choices.sort(key=lambda x: x[0])
    tolerance = max(6, hours * 0.35) * 3600
    return choices[0][1] if choices[0][0] <= tolerance else None


def ratio(cur: int, prev: int) -> float | None:
    if prev <= 0:
        return None
    return round((cur - prev) / prev, 4)


def main() -> int:
    now = datetime.now(KST)
    names = registry_names()
    rec = load("source_reconciliation_report.json", {})
    summary = rec.get("summary") or {}
    rows = rec.get("sources") or []
    by_name = {str(r.get("name")): r for r in rows if isinstance(r, dict) and r.get("name")}

    errors: list[str] = []
    expected = official_source_count()
    if int(summary.get("totalSources") or 0) != expected or int(summary.get("reconciledSources") or 0) != expected:
        errors.append("reconciliation source count does not match registry")
    if int(summary.get("missingAfter") or 0) != 0:
        errors.append("official reconciliation still has missing IDs")
    missing_rows = [name for name in names if name not in by_name]
    extra_rows = sorted(set(by_name) - set(names))
    if missing_rows:
        errors.append(f"reconciliation missing registry sources: {missing_rows}")
    if extra_rows:
        errors.append(f"reconciliation contains non-registry sources: {extra_rows}")

    latest = {name: "" for name in names}
    jobs = jobs_of(load("jobs.json", {}))
    for job in jobs:
        label = str(job.get("source") or "")
        d = date_key(job.get("registered"))
        if not d:
            continue
        for name in names:
            if name in label and d > latest[name]:
                latest[name] = d

    sources: dict[str, dict[str, Any]] = {}
    for name in names:
        row = by_name.get(name) or {}
        sources[name] = {
            "stableIdCount": int(row.get("officialIdCount") or 0),
            "latestRegistered": latest[name],
            "coverageComplete": row.get("coverageComplete") is True,
            "missingAfter": int(row.get("missingAfterCount") or 0),
            "accessErrors": int(row.get("accessErrors") or 0),
        }
        if row and (row.get("coverageComplete") is not True or int(row.get("missingAfterCount") or 0) != 0 or int(row.get("accessErrors") or 0) != 0):
            errors.append(f"source integrity failed: {name}")

    snapshot = {"generatedAt": now.isoformat(timespec="seconds"), "sources": sources}
    hist = load(str(HISTORY), {"version": 1, "snapshots": []})
    snaps = [x for x in (hist.get("snapshots") or []) if isinstance(x, dict)] if isinstance(hist, dict) else []
    baselines = {label: nearest_prior(snaps, now, hours) for label, hours in (("1d", 24), ("7d", 168), ("30d", 720))}

    comparisons: dict[str, Any] = {}
    anomalies: list[dict[str, Any]] = []
    for label, base in baselines.items():
        if not base:
            comparisons[label] = None
            continue
        old_sources = base.get("sources") or {}
        per_source: dict[str, Any] = {}
        for name in names:
            cur = sources[name]
            old = old_sources.get(name) or {}
            before = int(old.get("stableIdCount") or 0)
            after = int(cur.get("stableIdCount") or 0)
            per_source[name] = {
                "current": after,
                "baseline": before,
                "changeRatio": ratio(after, before),
                "latestRegistered": cur.get("latestRegistered") or "",
                "baselineLatestRegistered": old.get("latestRegistered") or "",
            }
            if label == "1d" and before >= 20:
                ch = ratio(after, before)
                if after == 0 or (ch is not None and ch <= -0.50):
                    anomalies.append({"severity": "warning", "code": "official-source-sudden-drop", "source": name, "baseline": before, "current": after, "changeRatio": ch})
            if label == "7d":
                latest_now = str(cur.get("latestRegistered") or "")
                latest_old = str(old.get("latestRegistered") or "")
                if latest_now and latest_old and latest_now == latest_old:
                    try:
                        age = (now.date() - datetime.fromisoformat(latest_now).date()).days
                    except ValueError:
                        age = 0
                    if age >= 7:
                        anomalies.append({"severity": "warning", "code": "official-source-freshness-stall", "source": name, "latestRegistered": latest_now, "ageDays": age})
        comparisons[label] = {"baselineAt": base.get("generatedAt", ""), "sources": per_source}

    if errors:
        anomalies.extend({"severity": "critical", "code": "official-source-trend-integrity", "message": e} for e in errors)

    report = {
        "generatedAt": snapshot["generatedAt"],
        "registrySourceCount": expected,
        "observedSourceCount": len(sources),
        "healthy": not errors,
        "sources": sources,
        "comparisons": comparisons,
        "anomalies": anomalies,
        "criticalCount": sum(1 for a in anomalies if a.get("severity") == "critical"),
        "warningCount": sum(1 for a in anomalies if a.get("severity") == "warning"),
        "policy": "exact registry sources; counts from stable-ID reconciliation; composite display labels never create synthetic sources",
    }
    snaps.append(snapshot)
    hist = {"version": 1, "updatedAt": snapshot["generatedAt"], "snapshots": snaps[-MAX_SNAPSHOTS:]}
    HISTORY.write_text(json.dumps(hist, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"healthy": report["healthy"], "sources": len(sources), "warnings": report["warningCount"]}, ensure_ascii=False))
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
