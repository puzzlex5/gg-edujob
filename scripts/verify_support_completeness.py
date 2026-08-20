#!/usr/bin/env python3
"""Strict publication gate for 25 Gyeonggi + 11 Seoul support-office completeness."""
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
            if pages >= 500:
                problems.append(f"{province}/{name}: emergency 500-page ceiling reached")
            if raw in {70, 80, 90, 100}:
                if board.get("coverageComplete") and pages < 500:
                    notes.append(f"{province}/{name}: round rawRows={raw}, but traversal ended without ceiling hit")
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

for note in notes:
    print("NOTE", note)
if problems:
    for problem in problems:
        print("FAIL", problem)
    raise SystemExit(f"Support-office completeness gate failed with {len(problems)} issue(s)")

print("Support-office completeness gate passed: Gyeonggi 25/25, Seoul 11/11 with board-level evidence")
