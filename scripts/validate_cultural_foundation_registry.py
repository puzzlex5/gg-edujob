#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

REGISTRY = Path("cultural_foundation_registry.json")
EXPECTED = {"서울": 24, "경기": 25}


def main() -> int:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    rows = [r for r in data.get("institutions", []) if r.get("enabled") is not False]
    errors: list[str] = []

    ids = [str(r.get("id") or "").strip() for r in rows]
    names = [str(r.get("name") or "").strip() for r in rows]
    if any(not x for x in ids):
        errors.append("blank institution id")
    if any(not x for x in names):
        errors.append("blank institution name")
    dup_ids = [k for k, v in Counter(ids).items() if v > 1]
    if dup_ids:
        errors.append(f"duplicate ids: {dup_ids}")

    by_region = Counter(str(r.get("region") or "") for r in rows)
    for region, expected in EXPECTED.items():
        if by_region.get(region, 0) != expected:
            errors.append(f"{region} registry count {by_region.get(region, 0)} != expected {expected}")

    alias_owner: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        rid = str(row.get("id") or "")
        region = str(row.get("region") or "")
        municipality = str(row.get("municipality") or "")
        level = str(row.get("level") or "")
        if region not in EXPECTED:
            errors.append(f"{rid}: unsupported region={region!r}")
        if not municipality:
            errors.append(f"{rid}: missing municipality")
        if level not in {"광역", "기초"}:
            errors.append(f"{rid}: invalid level={level!r}")
        aliases = [str(x).strip() for x in (row.get("aliases") or []) if str(x).strip()]
        for alias in [str(row.get("name") or "").strip(), *aliases]:
            alias_owner[alias].add(rid)

    ambiguous = {k: sorted(v) for k, v in alias_owner.items() if len(v) > 1}
    # Cross-region aliases such as "광주문화재단" are dangerous for automated matching.
    if ambiguous:
        errors.append(f"ambiguous aliases: {ambiguous}")

    declared = data.get("scope") or {}
    if int(declared.get("publicCulturalFoundations") or 0) != len(rows):
        errors.append("scope.publicCulturalFoundations does not match enabled registry rows")
    if int(declared.get("seoul") or 0) != EXPECTED["서울"]:
        errors.append("scope.seoul mismatch")
    if int(declared.get("gyeonggi") or 0) != EXPECTED["경기"]:
        errors.append("scope.gyeonggi mismatch")

    report = {
        "healthy": not errors,
        "enabledInstitutions": len(rows),
        "byRegion": dict(by_region),
        "missingOfficialRecruitmentUrl": sum(1 for r in rows if not str(r.get("officialRecruitmentUrl") or "").strip()),
        "missingHomepage": sum(1 for r in rows if not str(r.get("homepage") or "").strip()),
        "errors": errors,
    }
    Path("cultural_foundation_registry_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
