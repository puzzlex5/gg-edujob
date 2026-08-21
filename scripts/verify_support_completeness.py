#!/usr/bin/env python3
"""Strict publication gate for 25 Gyeonggi + 11 Seoul support-office completeness and exact links.

The gate persists auditable coverage evidence before deciding pass/fail. MirCMS can expose the
same physical board through multiple menu ids (mi). A stale menu alias must not make an otherwise
proven board look incomplete, but aliases are ignored only when the same physical board has a
successful representative and the alias itself returned zero rows without an access error.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
JOBS_PATH = ROOT / "jobs.json"
data = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
comp = data.get("supportCompleteness") or {}
problems = []
notes = []


def board_identity(url):
    """Identify a physical MirCMS board while ignoring menu-only routing parameters.

    bbsId is the stable board id. Query filters such as srch_aditCol1 and clasHmpgId remain part
    of the identity so a filtered view is not accidentally collapsed into an unfiltered board.
    """
    try:
        p = urlparse(url or "")
        q = parse_qs(p.query, keep_blank_values=True)
        bbs = (q.get("bbsId") or [""])[0]
        if not bbs:
            return url or ""
        filters = []
        for key in ("srch_aditCol1", "clasHmpgId"):
            if q.get(key):
                filters.append((key, q[key][0]))
        return (p.scheme.lower(), p.netloc.lower(), p.path, bbs, tuple(filters))
    except Exception:
        return url or ""


def reconcile_stale_aliases(office, province):
    """Collapse only evidence-proven stale menu aliases.

    Safe case: multiple URLs map to the same physical board, at least one traversed completely,
    and another alias returned zero rows with no access error/repetition. The zero-row alias is
    marked as ignored rather than being treated as a separate mandatory board.
    """
    boards = office.get("boardHealth") or []
    groups = {}
    for board in boards:
        groups.setdefault(board_identity(board.get("url", "")), []).append(board)

    ignored = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        completed = [b for b in group if b.get("coverageComplete")]
        if not completed:
            continue
        representative = max(
            completed,
            key=lambda b: (int(b.get("rawRows") or 0), int(b.get("pagesScanned") or 0)),
        )
        for alias in group:
            if alias is representative or alias.get("coverageComplete"):
                continue
            if int(alias.get("rawRows") or 0) != 0:
                continue
            if alias.get("accessError") or alias.get("paginationRepeated"):
                continue
            alias["ignoredAsStaleAlias"] = True
            alias["aliasOf"] = representative.get("url", "")
            ignored += 1
            notes.append(
                f"{province}/{office.get('name')}: ignored zero-row stale menu alias "
                f"{alias.get('url')} -> {representative.get('url')}"
            )

    effective = [b for b in boards if not b.get("ignoredAsStaleAlias")]
    if ignored and effective and all(b.get("coverageComplete") for b in effective):
        office["coverageComplete"] = True
        office["ok"] = True
        office["state"] = "complete"
        office["message"] = "최근 90일 범위 완전수집 · 중복/옛 메뉴 별칭 제외"
    return effective


expected = {"gyeonggi": 25, "seoul": 11}
report = {
    "generatedAt": datetime.now(timezone.utc).isoformat(),
    "supportCompleteness": comp,
    "supportLinkResolution": data.get("supportLinkResolution") or {},
    "provinces": {},
}

complete_counts = {"gyeonggi": 0, "seoul": 0}
for province, want in expected.items():
    statuses = data.get("sources", {}).get(province, {}).get("supportOffices", [])
    report["provinces"][province] = {
        "expectedOffices": want,
        "actualOffices": len(statuses),
        "offices": statuses,
    }
    if len(statuses) != want:
        problems.append(f"{province}: support-office status count {len(statuses)} != {want}")
    for office in statuses:
        name = office.get("name", "(unknown)")
        boards = reconcile_stale_aliases(office, province)
        if office.get("coverageComplete") and office.get("ok"):
            complete_counts[province] += 1
        else:
            problems.append(f"{province}/{name}: incomplete or unhealthy ({office.get('state')}: {office.get('message')})")
        if not boards:
            problems.append(f"{province}/{name}: no board-level coverage evidence")
            continue
        for board in boards:
            pages = int(board.get("pagesScanned") or 0)
            raw = int(board.get("rawRows") or 0)
            if not board.get("coverageComplete"):
                problems.append(f"{province}/{name}: board-level traversal is not complete ({board.get('url')})")
            if pages <= 0:
                problems.append(f"{province}/{name}: board has no pagination evidence ({board.get('url')})")
            if pages >= 500:
                problems.append(f"{province}/{name}: emergency 500-page ceiling reached")
            if board.get("accessError"):
                problems.append(f"{province}/{name}: board traversal stopped on access error before a proven boundary")
            if not (board.get("crossedLookback") or board.get("naturalEnd") or board.get("explicitEmpty")):
                problems.append(f"{province}/{name}: no explicit termination proof (90-day boundary / natural end / official empty)")
            if raw in {70, 80, 90, 100}:
                if board.get("coverageComplete") and pages < 500:
                    notes.append(f"{province}/{name}: round rawRows={raw}, but traversal has explicit completion evidence")
                else:
                    problems.append(f"{province}/{name}: suspicious round rawRows={raw} without proven complete traversal")

# Recompute completeness after evidence-based alias reconciliation and persist the corrected health.
if comp:
    comp["gyeonggiTotal"] = len(data.get("sources", {}).get("gyeonggi", {}).get("supportOffices", []))
    comp["seoulTotal"] = len(data.get("sources", {}).get("seoul", {}).get("supportOffices", []))
    comp["gyeonggiComplete"] = complete_counts["gyeonggi"]
    comp["seoulComplete"] = complete_counts["seoul"]
    comp["warnings"] = [
        o.get("name")
        for province in ("gyeonggi", "seoul")
        for o in data.get("sources", {}).get(province, {}).get("supportOffices", [])
        if not (o.get("coverageComplete") and o.get("ok"))
    ]
    report["supportCompleteness"] = comp
    if int(comp.get("gyeonggiTotal") or 0) != 25 or int(comp.get("seoulTotal") or 0) != 11:
        problems.append("supportCompleteness totals are not 25/11")
    if int(comp.get("gyeonggiComplete") or 0) != 25:
        problems.append(f"Gyeonggi complete offices: {comp.get('gyeonggiComplete')}/25")
    if int(comp.get("seoulComplete") or 0) != 11:
        problems.append(f"Seoul complete offices: {comp.get('seoulComplete')}/11")
else:
    problems.append("supportCompleteness metadata missing")

# Exact individual posting links are part of health, not a cosmetic enhancement.
link = data.get("supportLinkResolution") or {}
for province, exact_key, total_key in (
    ("gyeonggi", "gyeonggiExact", "gyeonggiTotal"),
    ("seoul", "seoulExact", "seoulTotal"),
):
    exact = int(link.get(exact_key) or 0)
    total = int(link.get(total_key) or 0)
    if total <= 0:
        problems.append(f"{province}: exact-link metadata missing or empty")
    elif exact != total:
        problems.append(f"{province}: exact individual posting links {exact}/{total}")

unresolved_jobs = [
    j for j in data.get("jobs", [])
    if j.get("sourceType") == "교육지원청 개별 게시판" and not j.get("detailLinkResolved")
]
if unresolved_jobs:
    problems.append(f"support-office jobs without exact individual links: {len(unresolved_jobs)}")
    for j in unresolved_jobs[:10]:
        notes.append(f"unresolved: {j.get('province')}/{j.get('source')} · {j.get('title')}")

# Persist reconciled health only after the strict evidence rules above have been applied.
JOBS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
report["notes"] = notes
report["problems"] = problems
report["gatePassed"] = not problems
(ROOT / "support_coverage_report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)

for note in notes:
    print("NOTE", note)
if problems:
    for problem in problems:
        print("FAIL", problem)
    raise SystemExit(f"Support-office completeness gate failed with {len(problems)} issue(s)")

print("Support-office gate passed: Gyeonggi 25/25, Seoul 11/11, board-level traversal proven, all individual links exact")
