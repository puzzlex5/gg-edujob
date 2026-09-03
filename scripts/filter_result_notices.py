#!/usr/bin/env python3
"""Remove non-recruitment result/personnel notices from the canonical official dataset.

This is intentionally title-policy-only and reuses scrape_jobs.EXCLUDE_WORDS so the fast
collector, reconciliation population, and final publication guard share one exclusion policy.
It does not use semantic similarity and therefore cannot delete a genuine posting merely
because its school/title/date resemble another row.
"""
import json
from pathlib import Path

import scrape_jobs as primary

ROOT = Path(__file__).resolve().parents[1]
JOBS_PATH = ROOT / "jobs.json"


def main():
    payload = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    kept = []
    removed = []
    for job in jobs:
        title = str((job or {}).get("title") or "")
        if primary.EXCLUDE_WORDS.search(title):
            removed.append({
                "id": (job or {}).get("id", ""),
                "title": title,
                "url": (job or {}).get("url", ""),
            })
        else:
            kept.append(job)
    payload["jobs"] = kept
    JOBS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"removedResultNotices": len(removed), "examples": removed[:20]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
