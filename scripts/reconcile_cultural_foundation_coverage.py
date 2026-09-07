#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from cultural_foundation_common import alias_index, generated_at, load_foundations, match_foundation, norm

SOURCES = {
    "official": Path("cultural_foundation_official_jobs.json"),
    "cleaneye": Path("cleaneye_foundation_jobs.json"),
    "artmore": Path("artmore_foundation_jobs.json"),
    "ancf": Path("ancf_foundation_jobs.json"),
}
LESSONINFO = Path("lessoninfo_jobs.json")
OFFICIAL_REPORT = Path("cultural_foundation_official_report.json")
ROUTE_REPORT = Path("cultural_foundation_routes_report.json")
CLEANEYE_REPORT = Path("cleaneye_foundation_report.json")
OUT = Path("cultural_foundation_coverage_report.json")

NOISE = re.compile(r"진행중|공고|채용|모집|직원|신규|공개경쟁|공개|재단법인|\(재\)|\[재\]", re.I)


def rows(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list): return data
        if isinstance(data, dict): return data.get("jobs", [])
    except Exception:
        pass
    return []


def title_key(title: str, foundation_name: str = "") -> str:
    text = str(title or "")
    if foundation_name:
        text = text.replace(foundation_name, "")
    text = NOISE.sub("", text)
    return norm(text)


def same_job(a: dict, b: dict) -> bool:
    if a.get("foundationRegistryId") != b.get("foundationRegistryId"):
        return False
    ka = title_key(a.get("title"), a.get("foundationName") or "")
    kb = title_key(b.get("title"), b.get("foundationName") or "")
    if not ka or not kb:
        return False
    if ka in kb or kb in ka:
        return min(len(ka), len(kb)) >= 7
    return SequenceMatcher(None, ka, kb).ratio() >= 0.74


def current(row: dict) -> bool:
    end = str(row.get("applyEnd") or "")
    return not end or end >= date.today().isoformat()


def main() -> int:
    foundations = load_foundations()
    pairs = alias_index(foundations)
    by_source = {name: [dict(x) for x in rows(path) if current(x)] for name, path in SOURCES.items()}

    lesson = []
    for raw in rows(LESSONINFO):
        if str(raw.get("sourceSurface") or "") != "culture-arts" or not current(raw):
            continue
        inst = match_foundation(" ".join(str(raw.get(k) or "") for k in ("title", "school", "rawListText")), pairs)
        if not inst:
            continue
        item = dict(raw)
        item["foundationRegistryId"] = inst["id"]
        item["foundationName"] = inst["name"]
        lesson.append(item)
    by_source["lessoninfo"] = lesson

    official_report = {}
    route_report = {}
    cleaneye_report = {}
    for path, target in ((OFFICIAL_REPORT, "official"), (ROUTE_REPORT, "route"), (CLEANEYE_REPORT, "cleaneye")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if target == "official": official_report = data
        elif target == "route": route_report = data
        else: cleaneye_report = data

    official_outcomes = {x.get("foundationRegistryId"): x for x in (official_report.get("institutions") or [])}
    institutions = []
    unrepresented = []
    for f in foundations:
        fid = f["id"]
        source_rows = {src: [x for x in rs if x.get("foundationRegistryId") == fid] for src, rs in by_source.items()}
        primary = source_rows["official"] + source_rows["cleaneye"]
        cross = source_rows["artmore"] + source_rows["ancf"] + source_rows["lessoninfo"]
        missing = []
        for x in cross:
            if not any(same_job(x, p) for p in primary):
                missing.append({
                    "source": x.get("source") or "lessoninfo",
                    "sourceIdentity": x.get("sourceIdentity"),
                    "title": x.get("title"),
                    "applyEnd": x.get("applyEnd") or "",
                    "auditUrl": x.get("auditUrl") or x.get("verifiedUrl") or x.get("originalUrl") or x.get("url") or "",
                })
        if missing:
            unrepresented.extend({"foundationRegistryId": fid, "foundationName": f["name"], **m} for m in missing)
        outcome = official_outcomes.get(fid)
        institutions.append({
            "foundationRegistryId": fid,
            "foundationName": f["name"],
            "region": f["region"],
            "municipality": f["municipality"],
            "officialCoverageStatus": (outcome or {}).get("status") or "missing-outcome",
            "counts": {src: len(v) for src, v in source_rows.items()},
            "unrepresentedCrossChecks": missing,
        })

    missing_outcomes = [x["foundationRegistryId"] for x in institutions if x["officialCoverageStatus"] == "missing-outcome"]
    source_counts = {src: len(v) for src, v in by_source.items()}
    hard_inputs_healthy = bool(route_report.get("healthy")) and bool(official_report.get("healthy")) and bool(cleaneye_report.get("healthy"))
    report = {
        "generatedAt": generated_at(),
        "policy": "cultural-foundation-49-primary-plus-three-crosschecks-v1",
        "healthy": hard_inputs_healthy and not missing_outcomes and not unrepresented,
        "foundationCount": len(foundations),
        "coverageOutcomeCount": len(institutions) - len(missing_outcomes),
        "missingCoverageOutcomes": missing_outcomes,
        "sourceCounts": source_counts,
        "unrepresentedCrossCheckCount": len(unrepresented),
        "unrepresentedCrossChecks": unrepresented,
        "institutions": institutions,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("healthy", "foundationCount", "coverageOutcomeCount", "missingCoverageOutcomes", "sourceCounts", "unrepresentedCrossCheckCount")}, ensure_ascii=False, indent=2))
    return 0 if report["healthy"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
