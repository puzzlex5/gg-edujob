#!/usr/bin/env python3
"""Repair support-office source attribution when a job URL proves the canonical office.

Some duplicate/recovery paths can retain a historical source string such as
"안성교육지원청 + 광명교육지원청" even though the exact detail URL belongs to one
specific official support-office host. That can mislead source displays and source-based
filters. Only records whose URL host maps unambiguously to exactly one configured support
office are changed; central portals and ambiguous hosts are left untouched.
"""
import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs.json"
SOURCES = ROOT / "sources.json"
REPORT = ROOT / "source_attribution_report.json"


def host_of(value):
    try:
        host = (urlparse(str(value or "")).hostname or "").lower().strip(".")
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def build_host_map(sources):
    owners = {}
    for province_key in ("gyeonggi", "seoul"):
        section = sources.get(province_key, {}) if isinstance(sources, dict) else {}
        for office in section.get("supportOffices", []) or []:
            name = str(office.get("name") or "").strip()
            if not name:
                continue
            urls = [office.get("url"), office.get("boardUrl")]
            urls.extend(office.get("boardUrls") or [])
            for raw in urls:
                host = host_of(raw)
                if host:
                    owners.setdefault(host, set()).add(name)
    return {host: next(iter(names)) for host, names in owners.items() if len(names) == 1}


def main():
    jobs_payload = json.loads(JOBS.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    jobs = jobs_payload.get("jobs", []) if isinstance(jobs_payload, dict) else []
    host_map = build_host_map(sources)

    changed = []
    for job in jobs:
        raw_url = job.get("url") or job.get("openUrl") or job.get("boardUrl")
        host = host_of(raw_url)
        canonical = host_map.get(host)
        if not canonical:
            continue
        old = str(job.get("source") or "").strip()
        if old == canonical:
            continue
        # Restrict repairs to support-office attribution. Do not overwrite central portal labels
        # unless the existing value already looks like an education-support-office source.
        if "교육지원청" not in old:
            continue
        job["source"] = canonical
        changed.append({
            "title": job.get("title"),
            "url": job.get("url"),
            "before": old,
            "after": canonical,
        })

    if changed:
        JOBS.write_text(json.dumps(jobs_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT.write_text(json.dumps({"changedCount": len(changed), "changed": changed}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"source attribution normalized: {len(changed)}")


if __name__ == "__main__":
    main()
