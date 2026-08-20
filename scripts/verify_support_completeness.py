#!/usr/bin/env python3
"""Strict publication gate for 25 Gyeonggi + 11 Seoul support-office completeness and exact links."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
data = json.loads((ROOT / "jobs.json").read_text(encoding="utf-8"))
comp = data.get("supportCompleteness") or {}
problems = []
notes = []

expected = {"gyeonggi": 25, "seoul": 11}
for province, want in expected.items():
    statuses = data.get("sources", {}).get(province, {}).get("supportOffices", [])
    if len(statuses) != want:
        problems.append(f"{province}: support-office status count {len(statuses)} != {want}")
    for office in statuses:
        name = office.get("name", "(unknown)")
        if not office.get("coverageComplete") or not office.get("ok"):
            problems.append(f"{province}/{name}: incomplete or unhealthy ({office.get('state')}: {office.get('message')})")
        boards = office.get("boardHealth") or []
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
                problems.append(f"{province}/{name}: board traversal stopped on access error")
            if raw in {70, 80, 90, 100}:
                if board.get("coverageComplete") and pages < 500:
                    notes.append(f"{province}/{name}: round rawRows={raw}, but traversal has explicit completion evidence")
                else:
                    problems.append(f"{province}/{name}: suspicious round rawRows={raw} without proven complete traversal")

if comp:
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

for note in notes:
    print("NOTE", note)
if problems:
    for problem in problems:
        print("FAIL", problem)
    raise SystemExit(f"Support-office completeness gate failed with {len(problems)} issue(s)")

print("Support-office gate passed: Gyeonggi 25/25, Seoul 11/11, board-level traversal proven, all individual links exact")
