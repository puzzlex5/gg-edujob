#!/usr/bin/env python3
"""Strict publication gate for 25 Gyeonggi + 11 Seoul support-office completeness and exact links.

The gate persists auditable coverage evidence before deciding pass/fail. MirCMS can expose the
same physical board through multiple menu ids (mi). A stale menu alias must not make an otherwise
proven board look incomplete, but aliases are ignored only when evidence proves that the same
physical board was traversed completely.

A recovery/deep-audit run may see a transient support-board access failure during its early
coverage pass and then successfully re-traverse that exact source during the later independent
38-source stable-ID reconciliation. When (and only when) that reconciliation belongs to the same
candidate jobs.json, reconciles all 38 sources, has zero missing IDs, and supplies complete
board-level traversal evidence, the later evidence supersedes the earlier transient status. This
keeps the gate fail-closed while avoiding false failures after a proven retry recovery.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from source_registry import official_source_count

ROOT = Path(__file__).resolve().parents[1]
JOBS_PATH = ROOT / "jobs.json"
RECON_PATH = ROOT / "source_reconciliation_report.json"
data = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
comp = data.get("supportCompleteness") or {}
problems = []
notes = []


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


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


def same_candidate_reconciliation():
    """Return later support traversal evidence only when it is bound to this jobs candidate."""
    report = load_json(RECON_PATH, {})
    summary = report.get("summary") or {}
    embedded = data.get("sourceReconciliation") or {}
    if not report or not summary or not embedded:
        return {}
    # reconcile_source_ids.py writes both the embedded summary and report from the same scan.
    # Matching generatedAt prevents a stale successful report from blessing a newer candidate.
    if str(summary.get("generatedAt") or "") != str(embedded.get("generatedAt") or ""):
        return {}
    expected_sources = official_source_count()
    if (
        int(summary.get("reconciledSources") or 0) != expected_sources
        or int(summary.get("totalSources") or 0) != expected_sources
    ):
        return {}
    if int(summary.get("missingAfter") or 0) != 0:
        return {}

    evidence = {}
    for src in report.get("sources") or []:
        province = src.get("province")
        name = src.get("name")
        if province not in ("경기", "서울") or not name or "교육지원청" not in name:
            continue
        # Do not infer health from counts. Reconciliation itself must have proven traversal and
        # reconciled every stable ID for this exact source.
        if not (src.get("coverageComplete") and src.get("reconciled")):
            continue
        boards = src.get("boardHealth") or []
        if not boards:
            continue
        effective = [b for b in boards if not b.get("ignoredAsDuplicateMenuAlias")]
        if not effective or not all(b.get("coverageComplete") for b in effective):
            continue
        if any(b.get("accessError") or b.get("paginationRepeated") for b in effective):
            continue
        evidence[(province, name)] = src
    return evidence


def apply_later_reconciliation_evidence(statuses, province_label, evidence):
    """Replace only an older unhealthy status with proven later same-candidate traversal."""
    for office in statuses:
        key = (province_label, office.get("name"))
        src = evidence.get(key)
        if not src:
            continue
        if office.get("coverageComplete") and office.get("ok"):
            continue
        boards = src.get("boardHealth") or []
        effective = [b for b in boards if not b.get("ignoredAsDuplicateMenuAlias")]
        if not effective or not all(b.get("coverageComplete") for b in effective):
            continue
        office["boards"] = src.get("boards") or office.get("boards") or []
        office["boardHealth"] = boards
        office["pagesScanned"] = int(src.get("pagesScanned") or sum(int(b.get("pagesScanned") or 0) for b in effective))
        office["rawRows"] = sum(int(b.get("rawRows") or 0) for b in effective)
        office["count"] = int(src.get("officialIdCount") or office.get("count") or 0)
        office["coverageComplete"] = True
        office["ok"] = True
        office["state"] = "complete"
        office["message"] = "최근 90일 범위 완전수집 · 후속 독립 ID 대조 재순회로 재확인"
        office["coverageEvidenceSource"] = "same-candidate-38-source-reconciliation"
        notes.append(
            f"{province_label}/{office.get('name')}: earlier transient coverage failure superseded "
            "by later same-candidate 38-source traversal proof"
        )


def reconcile_stale_aliases(office, province):
    """Collapse only evidence-proven stale/duplicate menu aliases.

    Two safe cases are accepted:
    1) a zero-row menu alias with no access/repetition error when another view of the same physical
       board completed; or
    2) an alias already marked ignoredAsDuplicateMenuAlias by the same-candidate stable-ID
       reconciliation, where its entire stable-ID set was proven to be a subset of the completed
       representative. The latter marker is trusted only because stale reports are rejected above.
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
            if alias.get("ignoredAsDuplicateMenuAlias"):
                alias["ignoredAsStaleAlias"] = True
                ignored += 1
                notes.append(
                    f"{province}/{office.get('name')}: ignored stable-ID-proven duplicate menu alias "
                    f"{alias.get('url')} -> {alias.get('aliasOf') or representative.get('url')}"
                )
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

    effective = [b for b in boards if not (b.get("ignoredAsStaleAlias") or b.get("ignoredAsDuplicateMenuAlias"))]
    if ignored and effective and all(b.get("coverageComplete") for b in effective):
        office["coverageComplete"] = True
        office["ok"] = True
        office["state"] = "complete"
        office["message"] = "최근 90일 범위 완전수집 · 중복/옛 메뉴 별칭 제외"
    return effective


expected = {"gyeonggi": 25, "seoul": 11}
province_labels = {"gyeonggi": "경기", "seoul": "서울"}
later_evidence = same_candidate_reconciliation()
report = {
    "generatedAt": datetime.now(timezone.utc).isoformat(),
    "supportCompleteness": comp,
    "supportLinkResolution": data.get("supportLinkResolution") or {},
    "provinces": {},
    "laterReconciliationEvidenceUsed": bool(later_evidence),
}

complete_counts = {"gyeonggi": 0, "seoul": 0}
for province, want in expected.items():
    statuses = data.get("sources", {}).get(province, {}).get("supportOffices", [])
    apply_later_reconciliation_evidence(statuses, province_labels[province], later_evidence)
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
