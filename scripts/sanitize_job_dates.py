#!/usr/bin/env python3
"""Remove impossible future registration dates without dropping recruitment postings.

Some support-office rows omit an explicit registration-date header. Legacy fallback parsing can
then mistake a contract/application date for the registration date. A registration timestamp
cannot be in the future, so keep the posting but clear/repair that metadata and record evidence.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs.json"
REPORT = ROOT / "date_anomaly_report.json"
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()


def parse_date(value):
    value = str(value or "").strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except Exception:
            pass
    return None


def main():
    data = json.loads(JOBS.read_text(encoding="utf-8"))
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    anomalies = []
    changed = 0

    for job in jobs:
        reg_raw = str(job.get("registered") or "").strip()
        reg = parse_date(reg_raw)
        if not reg or reg <= TODAY:
            continue

        before = {
            "registered": reg_raw,
            "applyStart": job.get("applyStart", ""),
            "applyEnd": job.get("applyEnd", ""),
            "workStart": job.get("workStart", ""),
            "workEnd": job.get("workEnd", ""),
        }

        # If another date field contains a plausible non-future date, prefer it only when it
        # represents an application start. Otherwise leave registration unknown rather than
        # fabricating one from a hiring/contract period.
        apply_start = parse_date(job.get("applyStart"))
        replacement = ""
        if apply_start and apply_start <= TODAY and (TODAY - apply_start).days <= 180:
            replacement = apply_start.strftime("%Y/%m/%d")

        job["registered"] = replacement
        # Seoul fallback historically copied the mistaken registration date into applyStart.
        if str(job.get("applyStart") or "").strip() == reg_raw:
            job["applyStart"] = ""

        anomalies.append({
            "province": job.get("province", ""),
            "source": job.get("source", ""),
            "school": job.get("school", ""),
            "title": job.get("title", ""),
            "url": job.get("url", ""),
            "before": before,
            "after": {
                "registered": job.get("registered", ""),
                "applyStart": job.get("applyStart", ""),
            },
            "reason": "future registration date is impossible; likely row-level fallback selected another period date",
        })
        changed += 1

    data["dateSanitization"] = {
        "checkedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "futureRegistrationDatesCorrected": changed,
    }
    JOBS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT.write_text(json.dumps({
        "generatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "count": changed,
        "anomalies": anomalies,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"date sanitization: corrected {changed} impossible future registration dates")


if __name__ == "__main__":
    main()
