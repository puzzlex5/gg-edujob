#!/usr/bin/env python3
"""Autonomous supervisor for 수도권에듀잡.

This process does not scrape or mutate recruitment data itself. It observes the
existing collector/audit evidence, selects at most one existing repair workflow,
and applies cooldowns so a transient fault cannot create a repair loop.

The design is intentionally conservative: over-collection and semantic-duplicate
warnings are surfaced for review but never trigger destructive cleanup.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
DEFAULT_COOLDOWN_HOURS = 4


def now_kst() -> datetime:
    return datetime.now(KST)


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        if s.endswith(" KST"):
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S KST").replace(tzinfo=KST)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except (ValueError, TypeError):
        return None


def age_hours(value: Any, now: datetime) -> float | None:
    dt = parse_time(value)
    if not dt:
        return None
    return max(0.0, (now - dt).total_seconds() / 3600)


def read_json(path: str | Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {} if default is None else default


def write_json(path: str | Path, data: Any) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Candidate:
    workflow: str
    code: str
    reason: str
    priority: int
    severity: str
    cooldown_hours: int = DEFAULT_COOLDOWN_HOURS


def is_success(entry: Any) -> bool:
    return isinstance(entry, dict) and entry.get("state") == "success" and entry.get("stage") == "complete"


def build_plan(
    collector: dict[str, Any],
    deep: dict[str, Any],
    quality: dict[str, Any],
    state: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    candidates: list[Candidate] = []
    observations: list[dict[str, Any]] = []
    signals: dict[str, Any] = {}

    fast = collector.get("fast", {}) if isinstance(collector, dict) else {}
    fast_age = age_hours(fast.get("lastSuccessAt"), now)
    signals["fast"] = {
        "state": fast.get("state", "missing"),
        "stage": fast.get("stage", "missing"),
        "lastSuccessAt": fast.get("lastSuccessAt", ""),
        "ageHours": None if fast_age is None else round(fast_age, 2),
        "jobsCount": fast.get("jobsCount"),
    }

    if not is_success(fast):
        candidates.append(Candidate(
            "update-jobs.yml", "fast-collector-unhealthy",
            f"빠른 수집기가 성공/완료 상태가 아닙니다: state={fast.get('state')} stage={fast.get('stage')}",
            105, "critical", 3,
        ))
    elif fast_age is None or fast_age > 5:
        candidates.append(Candidate(
            "update-jobs.yml", "fast-collector-stale",
            f"마지막 검증 수집 성공이 {('확인되지 않음' if fast_age is None else f'{fast_age:.1f}시간 전')}입니다.",
            90, "warning", 3,
        ))

    coverage = fast.get("coverageEvidence", {}) if isinstance(fast, dict) else {}
    rec = coverage.get("sourceReconciliation", {}) if isinstance(coverage, dict) else {}
    support = coverage.get("supportCoverage", {}) if isinstance(coverage, dict) else {}
    missing_official = int(rec.get("missingOfficialIdsFromCurrentDataset") or 0)
    missing_after = int(rec.get("missingAfter") or 0)
    reconciled = int(rec.get("reconciledSources") or 0)
    total_sources = int(rec.get("totalSources") or 0)
    central_complete = rec.get("centralPaginationComplete") is True
    exact_links = coverage.get("exactLinks") is True and support.get("exactLinks", True) is True
    current_complete = coverage.get("currentComplete") is True
    coverage_fresh = coverage.get("fresh") is True
    signals["coverage"] = {
        "fresh": coverage_fresh,
        "currentComplete": current_complete,
        "exactLinks": exact_links,
        "reconciledSources": reconciled,
        "totalSources": total_sources,
        "missingAfter": missing_after,
        "missingOfficialIds": missing_official,
        "centralPaginationComplete": central_complete,
    }

    hard_integrity_failure = bool(coverage) and (
        missing_official > 0
        or missing_after > 0
        or (total_sources > 0 and reconciled < total_sources)
        or not central_complete
        or not exact_links
        or not current_complete
    )
    if hard_integrity_failure:
        candidates.append(Candidate(
            "recover-missing-jobs.yml", "coverage-integrity-failure",
            "공식 ID/출처 대사·페이지네이션·상세링크 중 하나 이상이 완전성 기준을 통과하지 못했습니다.",
            130, "critical", 4,
        ))
    elif coverage and not coverage_fresh:
        candidates.append(Candidate(
            "daily-deep-audit.yml", "coverage-evidence-stale",
            "완전성 증거가 최신 상태가 아닙니다. 정밀감사를 다시 수행해야 합니다.",
            70, "warning", 8,
        ))

    effective_state = deep.get("effectiveState") or deep.get("state")
    effective_at = deep.get("effectiveAt") or deep.get("lastSuccessAt") or deep.get("updatedAt")
    deep_age = age_hours(effective_at, now)
    evidence = deep.get("effectiveEvidence", {}) if isinstance(deep, dict) else {}
    deep_complete = (
        effective_state == "success"
        and evidence.get("currentComplete", True) is True
        and int(evidence.get("missingAfter") or 0) == 0
        and int(evidence.get("missingOfficialIdsFromCurrentDataset") or 0) == 0
        and evidence.get("centralPaginationComplete", True) is True
        and evidence.get("supportCoverageCurrentComplete", True) is True
    )
    signals["deepAudit"] = {
        "effectiveState": effective_state or "missing",
        "effectiveAt": effective_at or "",
        "ageHours": None if deep_age is None else round(deep_age, 2),
        "complete": deep_complete,
        "proof": deep.get("effectiveProof", ""),
    }
    if not deep_complete:
        candidates.append(Candidate(
            "daily-deep-audit.yml", "deep-audit-unhealthy",
            "최종 정밀감사 증거가 성공/완전 상태가 아닙니다.",
            115, "critical", 6,
        ))
    elif deep_age is None or deep_age > 30:
        candidates.append(Candidate(
            "daily-deep-audit.yml", "deep-audit-stale",
            f"최종 정밀감사 증거가 {('확인되지 않음' if deep_age is None else f'{deep_age:.1f}시간 전')}입니다.",
            65, "warning", 8,
        ))

    qsummary = quality.get("summary", {}) if isinstance(quality, dict) else {}
    q_generated = quality.get("generatedAt") if isinstance(quality, dict) else None
    q_age = age_hours(q_generated, now)
    exact_id_dups = int(qsummary.get("exactStableIdDuplicateGroups") or 0)
    exact_url_dups = int(qsummary.get("exactUrlDuplicateGroups") or 0)
    missing_links = int(qsummary.get("missingDetailLinks") or 0)
    q_missing_ids = int(qsummary.get("missingOfficialIds") or 0)
    signals["quality"] = {
        "generatedAt": q_generated or "",
        "ageHours": None if q_age is None else round(q_age, 2),
        "exactStableIdDuplicateGroups": exact_id_dups,
        "exactUrlDuplicateGroups": exact_url_dups,
        "missingDetailLinks": missing_links,
        "missingOfficialIds": q_missing_ids,
        "resultLikeNotices": int(qsummary.get("resultLikeNotices") or 0),
        "staleInactiveOver120Days": int(qsummary.get("staleInactiveOver120Days") or 0),
        "publishedToOfficialRatio": qsummary.get("publishedToOfficialRatio"),
    }
    if q_missing_ids > 0 or missing_links > 0:
        candidates.append(Candidate(
            "recover-missing-jobs.yml", "quality-missing-evidence",
            f"운영 품질 감사에서 missingOfficialIds={q_missing_ids}, missingDetailLinks={missing_links}가 감지되었습니다.",
            125, "critical", 4,
        ))
    elif exact_id_dups > 0 or exact_url_dups > 0:
        candidates.append(Candidate(
            "operational-quality-audit.yml", "exact-duplicate-evidence",
            f"강한 중복 증거가 있습니다: stableId={exact_id_dups}, exactUrl={exact_url_dups}. 자동 삭제하지 않고 감사만 다시 수행합니다.",
            75, "warning", 12,
        ))
    elif q_age is None or q_age > 48:
        candidates.append(Candidate(
            "operational-quality-audit.yml", "quality-audit-stale",
            f"운영 품질 감사가 {('확인되지 않음' if q_age is None else f'{q_age:.1f}시간 전')}입니다.",
            45, "warning", 12,
        ))

    result_like = int(qsummary.get("resultLikeNotices") or 0)
    stale_inactive = int(qsummary.get("staleInactiveOver120Days") or 0)
    ratio = qsummary.get("publishedToOfficialRatio")
    if result_like or stale_inactive or (isinstance(ratio, (int, float)) and ratio > 2.0):
        observations.append({
            "code": "overcollection-review-only",
            "severity": "review",
            "message": f"과수집/결과공고 후보는 자동 삭제하지 않습니다: resultLike={result_like}, staleInactive={stale_inactive}, publishedToOfficialRatio={ratio}.",
        })

    candidates.sort(key=lambda x: (-x.priority, x.workflow, x.code))
    history = state.get("dispatchHistory", {}) if isinstance(state, dict) else {}
    blocked: list[dict[str, Any]] = []
    selected: Candidate | None = None
    for c in candidates:
        prior = history.get(c.workflow, {}) if isinstance(history, dict) else {}
        last_attempt = prior.get("lastAttemptAt") if isinstance(prior, dict) else None
        h = age_hours(last_attempt, now)
        if h is not None and h < c.cooldown_hours:
            blocked.append({
                **asdict(c),
                "hoursSinceLastAttempt": round(h, 2),
                "remainingCooldownHours": round(c.cooldown_hours - h, 2),
            })
            continue
        selected = c
        break

    if any(c.severity == "critical" for c in candidates):
        health = "critical"
    elif candidates or observations:
        health = "degraded"
    else:
        health = "healthy"

    return {
        "version": 1,
        "generatedAt": now.isoformat(timespec="seconds"),
        "health": health,
        "signals": signals,
        "observations": observations,
        "candidates": [asdict(c) for c in candidates],
        "blockedByCooldown": blocked,
        "selectedAction": asdict(selected) if selected else None,
        "policy": {
            "maxDispatchesPerRun": 1,
            "defaultCooldownHours": DEFAULT_COOLDOWN_HOURS,
            "destructiveCleanup": False,
            "rule": "repair verified completeness first; retain previous valid publication on failure",
        },
    }


def record_dispatch(state: dict[str, Any], plan: dict[str, Any], workflow: str, result: str, message: str, now: datetime) -> dict[str, Any]:
    out = state if isinstance(state, dict) else {}
    out.setdefault("version", 1)
    history = out.setdefault("dispatchHistory", {})
    prev = history.get(workflow, {}) if isinstance(history, dict) else {}
    selected = plan.get("selectedAction") or {}
    entry = {
        "lastAttemptAt": now.isoformat(timespec="seconds"),
        "lastResult": result,
        "lastReason": selected.get("reason", ""),
        "lastCode": selected.get("code", ""),
        "message": message,
        "count": int(prev.get("count") or 0) + 1,
    }
    history[workflow] = entry
    recent = out.setdefault("recentDispatches", [])
    recent.append({"workflow": workflow, **entry})
    out["recentDispatches"] = recent[-20:]
    out["lastUpdatedAt"] = now.isoformat(timespec="seconds")
    out["lastHealth"] = plan.get("health", "unknown")
    out["lastPlanSummary"] = {
        "generatedAt": plan.get("generatedAt", ""),
        "selectedAction": selected,
        "blockedByCooldown": plan.get("blockedByCooldown", []),
    }
    return out


def parse_now(value: str | None) -> datetime:
    if not value:
        return now_kst()
    dt = parse_time(value)
    if not dt:
        raise SystemExit(f"Invalid --now value: {value}")
    return dt


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--collector", default="collector_status.json")
    p.add_argument("--deep", default="deep_audit_status.json")
    p.add_argument("--quality", default="operational_quality_report.json")
    p.add_argument("--state", default="agent_status.json")
    p.add_argument("--output", default="agent_plan.json")
    p.add_argument("--now")

    r = sub.add_parser("record")
    r.add_argument("--state", default="agent_status.json")
    r.add_argument("--plan", default="agent_plan.json")
    r.add_argument("--workflow", required=True)
    r.add_argument("--result", choices=["dispatched", "failed"], required=True)
    r.add_argument("--message", default="")
    r.add_argument("--now")

    args = ap.parse_args()
    now = parse_now(args.now)
    if args.cmd == "plan":
        plan = build_plan(
            read_json(args.collector, {}),
            read_json(args.deep, {}),
            read_json(args.quality, {}),
            read_json(args.state, {}),
            now,
        )
        write_json(args.output, plan)
        action = plan.get("selectedAction")
        print(json.dumps({"health": plan["health"], "selectedAction": action}, ensure_ascii=False))
        return 0

    state = read_json(args.state, {})
    plan = read_json(args.plan, {})
    updated = record_dispatch(state, plan, args.workflow, args.result, args.message, now)
    write_json(args.state, updated)
    print(json.dumps(updated["dispatchHistory"].get(args.workflow, {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
