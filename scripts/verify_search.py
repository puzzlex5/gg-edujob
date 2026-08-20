#!/usr/bin/env python3
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "jobs.json"
INDEX = ROOT / "index.html"


def norm(value):
    s = unicodedata.normalize("NFKC", str(value or "")).lower()
    s = re.sub(r"[\u200b-\u200d\ufeff]", "", s)
    s = re.sub(r"[^0-9a-z가-힣]+", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def tokens(q):
    return [x for x in norm(q).split(" ") if x]


def hay(job):
    regions = job.get("regions") or []
    if not isinstance(regions, list):
        regions = []
    fields = [
        job.get("school"), job.get("title"), job.get("subject"), job.get("region"),
        " ".join(map(str, regions)), job.get("type"), job.get("source"),
        job.get("schoolLevel"), job.get("province"),
    ]
    return norm(" ".join(str(x or "") for x in fields))


def matches(job, query):
    h = hay(job)
    ts = tokens(query)
    return all(t in h for t in ts)


def meaningful_piece(value):
    parts = [p for p in tokens(value) if len(p) >= 2 and not re.fullmatch(r"\d+", p)]
    return parts[0] if parts else ""


def main():
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    jobs = raw.get("jobs", []) if isinstance(raw, dict) else raw
    if not isinstance(jobs, list) or len(jobs) < 100:
        raise SystemExit("Search verification cannot run: jobs dataset is missing or too small")

    html = INDEX.read_text(encoding="utf-8")
    required = ["const searchTokens=q=>", "const matchesQuery=(j,q)=>", "if(state.q&&!matchesQuery(j,state.q))return false"]
    missing = [x for x in required if x not in html]
    if missing:
        raise SystemExit("Frontend search implementation missing: " + ", ".join(missing))

    eligible = []
    for j in jobs:
        school = meaningful_piece(j.get("school"))
        title = meaningful_piece(j.get("title"))
        subject = meaningful_piece(j.get("subject"))
        if school or title or subject:
            eligible.append((j, school, title, subject))

    if len(eligible) < 30:
        raise SystemExit(f"Too few searchable postings for verification: {len(eligible)}")

    # Deterministic spread through the dataset so every hourly run tests many different sources/positions.
    sample_count = min(80, len(eligible))
    step = max(1, len(eligible) // sample_count)
    sample = eligible[::step][:sample_count]
    failures = []
    checks = 0

    for j, school, title, subject in sample:
        queries = []
        if school:
            queries.append(school)
        if subject:
            queries.append(subject)
        if title:
            queries.append(title)
        if school and subject:
            queries.append(f"{school} {subject}")
        elif school and title:
            queries.append(f"{school} {title}")

        for q in queries[:4]:
            checks += 1
            if not matches(j, q):
                failures.append({"query": q, "school": j.get("school"), "title": j.get("title")})

    # Whole-dataset sanity: every non-empty school/subject token sampled from the data must resolve to >=1 record.
    unique_queries = []
    seen = set()
    for j, school, title, subject in eligible:
        for q in (school, subject, title):
            if q and q not in seen:
                seen.add(q)
                unique_queries.append(q)
            if len(unique_queries) >= 120:
                break
        if len(unique_queries) >= 120:
            break

    for q in unique_queries:
        checks += 1
        if not any(matches(j, q) for j in jobs):
            failures.append({"query": q, "reason": "no job resolved from a token present in jobs.json"})

    if failures:
        print(json.dumps(failures[:20], ensure_ascii=False, indent=2))
        raise SystemExit(f"Search verification failed: {len(failures)} failures across {checks} checks")

    print(f"Search verification passed: {checks} checks across {sample_count} sampled postings")


if __name__ == "__main__":
    main()
