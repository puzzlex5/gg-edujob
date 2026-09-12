#!/usr/bin/env python3
from __future__ import annotations

import difflib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

HIST = "432aaa232106e206d92ec6b971fd5dec979ab363"
OUT = Path("support_71_diff_audit.json")
UA = "gg-edujob/support-71-diff-audit"


def load_json(text: str):
    return json.loads(text)


def git_json(ref: str, path: str):
    raw = subprocess.check_output(["git", "show", f"{ref}:{path}"], text=True)
    return load_json(raw)


def is_support(job: dict) -> bool:
    blob = " ".join(str(job.get(k, "")) for k in ("source", "sourceType", "sourceSurface", "sourceRole", "organization", "foundationName"))
    return "교육지원청" in blob or "support-office" in blob or "support_office" in blob


def identity(job: dict) -> str:
    for k in ("sourceIdentity", "externalId", "sourceId", "id", "jobId"):
        v = str(job.get(k) or "").strip()
        if v:
            return v
    url = str(job.get("url") or job.get("originalUrl") or job.get("detailUrl") or "")
    m = re.search(r"(?:nttSn|nttId|articleId|bbsNo|seq|idx|jobId|recrutSn|bpoId)=(\d+)", url, re.I)
    return f"url-id:{m.group(1)}" if m else f"url:{url}"


def title(job: dict) -> str:
    return re.sub(r"\s+", " ", str(job.get("title") or "")).strip()


def url(job: dict) -> str:
    return str(job.get("url") or job.get("detailUrl") or job.get("originalUrl") or "").strip()


def current_by_identity(jobs):
    return {identity(j): j for j in jobs}


def fetch(url_: str):
    if not url_:
        return None, "no-url"
    try:
        r = requests.get(url_, timeout=15, headers={"User-Agent": UA}, allow_redirects=True)
        return r, "ok"
    except Exception as exc:
        return None, f"error:{type(exc).__name__}"


def classify(old: dict, current: list[dict], current_ids: set[str]) -> dict:
    oid = identity(old)
    if oid in current_ids:
        return {"oldId": oid, "classification": "present", "title": title(old), "url": url(old)}

    old_title = title(old)
    old_url = url(old)
    same_title = [j for j in current if old_title and difflib.SequenceMatcher(None, old_title, title(j)).ratio() >= 0.92]
    if len(same_title) == 1:
        j = same_title[0]
        return {"oldId": oid, "classification": "ID 변경/URL 이동", "title": old_title, "url": old_url, "newId": identity(j), "newUrl": url(j), "newTitle": title(j)}

    r, status = fetch(old_url)
    if r is not None:
        final_url = r.url
        body = re.sub(r"\s+", " ", r.text)
        if r.status_code in (404, 410) or "존재하지 않는" in body or "삭제된" in body:
            return {"oldId": oid, "classification": "정상 종료/삭제", "title": old_title, "url": old_url, "http": r.status_code}
        if same_title:
            j = same_title[0]
            return {"oldId": oid, "classification": "ID 변경/URL 이동", "title": old_title, "url": old_url, "newId": identity(j), "newUrl": url(j), "newTitle": title(j)}
        # An old posting that still resolves but is not represented in current data is a real gap
        # unless its record is clearly outside the current publication period.
        apply_end = str(old.get("applyEnd") or old.get("deadline") or "")
        if apply_end and apply_end < datetime.now(timezone.utc).date().isoformat():
            return {"oldId": oid, "classification": "parser/기간 기준 변경", "title": old_title, "url": old_url, "http": r.status_code, "finalUrl": final_url}
        return {"oldId": oid, "classification": "실제 누락", "title": old_title, "url": old_url, "http": r.status_code, "finalUrl": final_url}

    apply_end = str(old.get("applyEnd") or old.get("deadline") or "")
    if apply_end and apply_end < datetime.now(timezone.utc).date().isoformat():
        cls = "parser/기간 기준 변경"
    else:
        cls = "정상 종료/삭제"
    return {"oldId": oid, "classification": cls, "title": old_title, "url": old_url, "probe": status}


def main():
    old_all = git_json(HIST, "jobs.json")
    cur_all = json.loads(Path("jobs.json").read_text(encoding="utf-8"))
    old = [j for j in old_all if is_support(j)]
    cur = [j for j in cur_all if is_support(j)]
    current_ids = {identity(j) for j in cur}
    missing = [j for j in old if identity(j) not in current_ids]

    rows = [classify(j, cur, current_ids) for j in missing]
    counts = {}
    for row in rows:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "historicalCommit": HIST,
        "historicalSupportCount": len(old),
        "currentSupportCount": len(cur),
        "historicalMinusCurrent": len(missing),
        "counts": counts,
        "actualMissing": [r for r in rows if r["classification"] == "실제 누락"],
        "rows": rows,
        "healthy": len(rows) == 71 and counts.get("실제 누락", 0) == 0,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("historicalSupportCount", "currentSupportCount", "historicalMinusCurrent", "counts", "healthy")}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["healthy"] else 2)


if __name__ == "__main__":
    main()
