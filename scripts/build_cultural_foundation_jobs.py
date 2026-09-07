#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from cultural_foundation_common import generated_at, norm

OFFICIAL = Path("cultural_foundation_official_jobs.json")
CLEANEYE = Path("cleaneye_foundation_jobs.json")
OFFICIAL_REPORT = Path("cultural_foundation_official_link_report.json")
CLEANEYE_REPORT = Path("cleaneye_foundation_report.json")
OUT = Path("cultural_foundation_jobs.json")
REPORT = Path("cultural_foundation_source_report.json")
NOISE = re.compile(r"진행중|공고|채용|모집|직원|신규|공개경쟁|공개|재단법인|\(재\)|\[재\]", re.I)


def load_rows(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list): return data
        if isinstance(data, dict): return data.get("jobs", [])
    except Exception:
        pass
    return []


def load_report(path: Path):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return {}


def key(row: dict) -> str:
    title = NOISE.sub("", str(row.get("title") or ""))
    foundation = str(row.get("foundationName") or "")
    title = title.replace(foundation, "")
    return norm(title)


def same_job(a: dict, b: dict) -> bool:
    if a.get("foundationRegistryId") != b.get("foundationRegistryId"):
        return False
    ka, kb = key(a), key(b)
    if not ka or not kb: return False
    if ka in kb or kb in ka: return min(len(ka), len(kb)) >= 7
    return SequenceMatcher(None, ka, kb).ratio() >= 0.78


def canonicalize(row: dict, provenance: str) -> dict:
    out = dict(row)
    out["source"] = "문화재단 공식채용"
    out["sourceType"] = "공식기관 채용"
    out["trustLevel"] = "공식원문" if provenance == "foundation-official" else "정부공식포털"
    out["sourceSurface"] = "cultural-foundation-primary"
    out["sourceSurfaceLabel"] = "문화재단 공식 채용"
    out["category"] = "private-recruitment"
    out["provenance"] = provenance
    return out


def main() -> int:
    official = [canonicalize(x, "foundation-official") for x in load_rows(OFFICIAL)]
    cleaneye = [canonicalize(x, "cleaneye") for x in load_rows(CLEANEYE)]
    merged = list(official)
    aliases = 0
    for row in cleaneye:
        if any(same_job(row, prior) for prior in official):
            aliases += 1
            continue
        merged.append(row)
    seen = set(); final = []
    for row in merged:
        sid = str(row.get("sourceIdentity") or "")
        if not sid or sid in seen: continue
        seen.add(sid); final.append(row)
    orep, crep = load_report(OFFICIAL_REPORT), load_report(CLEANEYE_REPORT)
    healthy = bool(orep.get("healthy")) and bool(crep.get("healthy"))
    traversal = bool(orep.get("traversalComplete")) and bool(crep.get("traversalComplete"))
    report = {
        "generatedAt": generated_at(),
        "source": "문화재단 공식채용",
        "publicationEnabled": healthy and traversal,
        "healthy": healthy,
        "traversalComplete": traversal,
        "missingAfterCount": 0,
        "officialDirectJobs": len(official),
        "cleaneyeSupplementJobs": len(cleaneye),
        "cleaneyeAliasesSuppressed": aliases,
        "publishedJobs": len(final),
        "errors": [],
    }
    OUT.write_text(json.dumps({"generatedAt": generated_at(), "source": "문화재단 공식채용", "jobs": final}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["publicationEnabled"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
