#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
REGISTRY = Path("cultural_foundation_registry.json")
OVERRIDES = Path("verified_foundation_official_sources.json")
CLEANEYE = Path("cleaneye_foundation_jobs.json")
ANCF = Path("ancf_foundation_jobs.json")
ARTMORE = Path("artmore_foundation_crosscheck.json")
LESSONINFO = Path("lessoninfo_jobs.json")
UNIFIED = Path("unified_jobs.json")
OFFICIAL = Path("official_foundation_jobs.json")
REPORT = Path("cultural_foundation_coverage_report.json")


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def rows(data):
    if isinstance(data, list): return data
    if isinstance(data, dict): return data.get("jobs", [])
    return []


def norm(s: str) -> str:
    s = re.sub(r"\(\s*재\s*\)|재단법인|진행중|마감", "", str(s or ""), flags=re.I)
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", s).lower()


def tokenish(s: str) -> set[str]:
    raw = re.sub(r"[^0-9A-Za-z가-힣]+", " ", str(s or "")).split()
    stop = {"채용", "공고", "모집", "직원", "공개채용", "공개경쟁채용", "재단법인", "진행중"}
    return {x.lower() for x in raw if len(x) >= 2 and x not in stop}


def title_match(a: str, b: str) -> bool:
    na, nb = norm(a), norm(b)
    if not na or not nb: return False
    if na == nb or (len(na) >= 12 and na in nb) or (len(nb) >= 12 and nb in na):
        return True
    ta, tb = tokenish(a), tokenish(b)
    if not ta or not tb: return False
    inter = len(ta & tb)
    return inter >= 2 and inter / max(1, min(len(ta), len(tb))) >= 0.65


def current(row: dict, today: date) -> bool:
    end = str(row.get("applyEnd") or row.get("pubEndDate") or "")[:10]
    if end:
        try:
            if date.fromisoformat(end) < today: return False
        except Exception:
            pass
    registered = str(row.get("registered") or row.get("pubDate") or "")[:10]
    if registered:
        try:
            if date.fromisoformat(registered) > today: return False
        except Exception:
            pass
    return True


def effective_foundations(registry: dict) -> list[dict]:
    foundations = [dict(x) for x in registry.get("institutions", []) if x.get("enabled") is not False]
    by_id = {str(x.get("id") or ""): x for x in foundations}
    overrides = load(OVERRIDES, {})
    for item in overrides.get("sources", []) if isinstance(overrides, dict) else []:
        fid = str(item.get("foundationRegistryId") or "")
        target = by_id.get(fid)
        if not target:
            continue
        for key in ("homepage", "officialRecruitmentUrl"):
            value = str(item.get(key) or "").strip()
            if value:
                target[key] = value
    return foundations


def foundation_matchers(registry_rows):
    out = []
    for f in registry_rows:
        for alias in [f.get("name"), *(f.get("aliases") or [])]:
            n = norm(alias)
            if n: out.append((n, f["id"]))
    out.sort(key=lambda x: len(x[0]), reverse=True)
    return out


def identify_foundation(row: dict, matchers) -> str:
    explicit = str(row.get("foundationRegistryId") or "")
    if explicit: return explicit
    hay = norm(" ".join(str(row.get(k) or "") for k in ("title","organization","school","rawListText")))
    for alias, fid in matchers:
        if alias in hay: return fid
    return ""


def same_date(a: dict, b: dict, key: str) -> bool:
    av = str(a.get(key) or "")[:10]
    bv = str(b.get(key) or "")[:10]
    return bool(av and bv and av == bv)


def executive_aggregate_represented(job: dict, candidates: list[dict]) -> bool:
    """Conservatively recognize one cross-check aggregate represented by split official posts.

    Some foundation syndication feeds combine 대표이사 + 비상임 이사/감사 into one row while the
    official board publishes separate detail posts. We accept aggregate equivalence only when at
    least two official candidates from the same foundation share the exact application deadline and
    registration date, and collectively contain representative-director and executive-role signals.
    This avoids weakening ordinary one-to-one title matching.
    """
    title = str(job.get("title") or "")
    if not re.search(r"임원", title) or not re.search(r"대표이사", title):
        return False
    end = str(job.get("applyEnd") or "")[:10]
    reg = str(job.get("registered") or job.get("pubDate") or "")[:10]
    if not end or not reg:
        return False
    matched = [u for u in candidates if str(u.get("applyEnd") or "")[:10] == end and str(u.get("registered") or "")[:10] == reg]
    if len(matched) < 2:
        return False
    titles = " ".join(str(u.get("title") or "") for u in matched)
    return bool(re.search(r"대표이사", titles) and re.search(r"비상임\s*임원|이사|감사", titles))


def represented_in_unified(job: dict, unified_rows: list[dict], fid: str, foundation_name: str) -> bool:
    title = str(job.get("title") or "")
    f_norm = norm(foundation_name)
    candidates = []
    for u in unified_rows:
        explicit_fid = str(u.get("foundationRegistryId") or "")
        utext = norm(" ".join(str(u.get(k) or "") for k in ("title","school","organization","searchText","source")))
        if explicit_fid:
            if explicit_fid != fid:
                continue
        elif f_norm and f_norm not in utext:
            continue
        candidates.append(u)
    if any(title_match(title, str(u.get("title") or "")) for u in candidates):
        return True
    return executive_aggregate_represented(job, candidates)


def main() -> int:
    today = datetime.now(KST).date()
    registry = load(REGISTRY, {})
    foundations = effective_foundations(registry)
    by_fid = {x["id"]:x for x in foundations}
    matchers = foundation_matchers(foundations)
    unified_rows = rows(load(UNIFIED, {}))

    datasets = {
        "official": rows(load(OFFICIAL, {})),
        "cleaneye": rows(load(CLEANEYE, {})),
        "artmore": rows(load(ARTMORE, {})),
        "ancf": rows(load(ANCF, {})),
        "lessoninfo-discovery": [x for x in rows(load(LESSONINFO, {})) if str(x.get("sourceSurface") or "") == "culture-arts"],
    }
    source_strength = {"official":"primary","cleaneye":"strong-cross-check","artmore":"strong-cross-check","ancf":"cross-check","lessoninfo-discovery":"discovery-only"}

    mapped = defaultdict(lambda: defaultdict(list))
    unmapped = defaultdict(list)
    for source, dataset in datasets.items():
        for job in dataset:
            if not current(job, today):
                continue
            fid = identify_foundation(job, matchers)
            if fid and fid in by_fid:
                mapped[fid][source].append(job)
            elif source != "lessoninfo-discovery":
                unmapped[source].append(job)

    gaps = []
    discovery_gaps = []
    institutions = []
    for f in foundations:
        fid = f["id"]
        source_counts = {s: len(mapped[fid].get(s, [])) for s in datasets}
        strong_seen = sum(source_counts[s] for s in ("official","cleaneye","artmore","ancf"))
        for source, jobs in mapped[fid].items():
            for job in jobs:
                represented = represented_in_unified(job, unified_rows, fid, f["name"])
                if represented:
                    continue
                gap = {
                    "foundationRegistryId":fid,
                    "foundationName":f["name"],
                    "source":source,
                    "strength":source_strength[source],
                    "sourceIdentity":job.get("sourceIdentity"),
                    "title":job.get("title"),
                    "registered":job.get("registered") or job.get("pubDate"),
                    "applyEnd":job.get("applyEnd"),
                    "url":job.get("url") or job.get("originalUrl") or job.get("auditUrl"),
                }
                if source == "lessoninfo-discovery": discovery_gaps.append(gap)
                else: gaps.append(gap)
        institutions.append({
            "foundationRegistryId":fid,
            "foundationName":f["name"],
            "region":f["region"],
            "municipality":f["municipality"],
            "sourceCounts":source_counts,
            "currentStrongSignals":strong_seen,
            "officialBoardConfigured":bool(str(f.get("officialRecruitmentUrl") or "").strip()),
            "coverageOutcome":"current-seen" if strong_seen else "no-current-crosscheck-signal",
        })

    component_paths = {
        "official": Path("official_foundation_report.json"),
        "cleaneye": Path("cleaneye_foundation_report.json"),
        "ancf": Path("ancf_foundation_report.json"),
        "artmore": Path("artmore_foundation_crosscheck_report.json"),
        "registry": Path("cultural_foundation_registry_report.json"),
    }
    component_reports = {k: load(path, {}) for k, path in component_paths.items()}
    component_available = {k: path.exists() for k, path in component_paths.items()}
    component_health = {
        k: (bool(component_reports[k].get("healthy")) if component_available[k] else None)
        for k in component_paths
    }
    failed_available_components = [
        k for k in component_paths
        if component_available[k] and component_health[k] is False
    ]
    configured_official = sum(1 for f in foundations if str(f.get("officialRecruitmentUrl") or "").strip())
    report = {
        "generatedAt":datetime.now(KST).isoformat(timespec="seconds"),
        "policy":"official-primary+multi-sensor-gap-detection-v3",
        "healthy": not failed_available_components and not gaps,
        "registryInstitutions":len(foundations),
        "officialBoardsConfigured":configured_official,
        "officialCoverageComplete":configured_official == len(foundations),
        "componentAvailable":component_available,
        "componentHealth":component_health,
        "failedAvailableComponents":failed_available_components,
        "currentMappedBySource":{s:sum(len(mapped[fid].get(s,[])) for fid in by_fid) for s in datasets},
        "coverageGapCount":len(gaps),
        "coverageGaps":gaps[:200],
        "discoveryOnlyGapCount":len(discovery_gaps),
        "discoveryOnlyGaps":discovery_gaps[:200],
        "unmappedStrongSourceRows":{s:len(v) for s,v in unmapped.items()},
        "institutions":institutions,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k:report[k] for k in ("healthy","registryInstitutions","officialBoardsConfigured","officialCoverageComplete","componentAvailable","componentHealth","failedAvailableComponents","currentMappedBySource","coverageGapCount","discoveryOnlyGapCount")}
    summary["coverageGaps"] = report["coverageGaps"][:20]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report["healthy"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
