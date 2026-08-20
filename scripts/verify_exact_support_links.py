#!/usr/bin/env python3
"""Publication-grade check that every support-office job has an exact posting target."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
payload = json.loads((ROOT / "jobs.json").read_text(encoding="utf-8"))
report = json.loads((ROOT / "support_link_resolution.json").read_text(encoding="utf-8"))
problems = []
notes = []

meta = payload.get("supportLinkResolution") or {}
for province, exact_key, total_key in (
    ("gyeonggi", "gyeonggiExact", "gyeonggiTotal"),
    ("seoul", "seoulExact", "seoulTotal"),
):
    exact = int(meta.get(exact_key) or 0)
    total = int(meta.get(total_key) or 0)
    if total <= 0:
        problems.append(f"{province}: no support-office jobs represented in link-resolution metadata")
    elif exact != total:
        problems.append(f"{province}: exact links {exact}/{total}")

bad_jobs = []
for job in payload.get("jobs", []):
    if job.get("sourceType") != "교육지원청 개별 게시판":
        continue
    if not job.get("detailLinkResolved"):
        bad_jobs.append((job.get("province"), job.get("source"), job.get("title")))
if bad_jobs:
    problems.append(f"support-office jobs without exact detail target: {len(bad_jobs)}")
    for row in bad_jobs[:10]:
        notes.append("unresolved job: " + " / ".join(str(x or "") for x in row))

for office in report.get("gyeonggi", {}).get("offices", []):
    name = office.get("name", "(unknown)")
    for board in office.get("boards", []):
        pages = int(board.get("pagesScanned") or 0)
        resolved = int(board.get("resolvedRows") or 0)
        reason = board.get("stopReason", "")
        if board.get("ceilingHit") or pages >= 500:
            problems.append(f"{name}: exact-link resolver hit emergency 500-page ceiling")
        if reason == "unresolved-row-structure":
            problems.append(f"{name}: board contains candidate rows whose exact IDs could not be parsed")
        if pages <= 0:
            problems.append(f"{name}: no exact-link pagination evidence for {board.get('board')}")
        if resolved in {70, 80, 90, 100}:
            notes.append(f"{name}: round resolvedRows={resolved}, pages={pages}, stop={reason or 'legacy-report'}")

for note in notes:
    print("NOTE", note)
if problems:
    for problem in problems:
        print("FAIL", problem)
    raise SystemExit(f"Exact support-link gate failed with {len(problems)} issue(s)")

print("Exact support-link gate passed: every support-office job has a resolved individual posting target")
