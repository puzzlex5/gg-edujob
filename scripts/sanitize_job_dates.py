#!/usr/bin/env python3
"""Repair impossible registration dates and obvious row-parser labels without dropping jobs.

Some support-office rows omit an explicit registration-date header. Legacy fallback parsing can
then mistake a contract/application date for the registration date. A registration timestamp
cannot be in the future, so keep the posting but clear/repair that metadata and record evidence.

A separate row-parser quirk can leak column labels such as ``기관명`` or ``학교명`` into the
school value (for example ``기관명 상동중학교``). Those labels are presentation metadata, not
part of the institution name, so strip only those exact leading labels. This cleanup is deliberately
conservative: it does not guess a school when the value is ambiguous.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs.json"
REPORT = ROOT / "date_anomaly_report.json"
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
SCHOOL_LABEL_RE = re.compile(r"^(?:기관명|학교명)\s*[:：]?\s+")


def parse_date(value):
    value = str(value or "").strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except Exception:
            pass
    return None


def clean_school(value):
    raw = str(value or "").strip()
    cleaned = SCHOOL_LABEL_RE.sub("", raw, count=1).strip()
    return cleaned if cleaned else raw


def main():
    data = json.loads(JOBS.read_text(encoding="utf-8"))
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    anomalies = []
    date_changed = 0
    school_labels_changed = 0

    for job in jobs:
        before_school = str(job.get("school") or "").strip()
        after_school = clean_school(before_school)
        if before_school and after_school != before_school:
            job["school"] = after_school
            school_labels_changed += 1

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
        date_changed += 1

    checked_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    data["dateSanitization"] = {
        "checkedAt": checked_at,
        "futureRegistrationDatesCorrected": date_changed,
    }
    data["metadataSanitization"] = {
        "checkedAt": checked_at,
        "schoolColumnLabelsRemoved": school_labels_changed,
    }
    JOBS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT.write_text(json.dumps({
        "generatedAt": checked_at,
        "count": date_changed,
        "anomalies": anomalies,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "sanitization: corrected "
        f"{date_changed} impossible future registration dates; "
        f"removed {school_labels_changed} school-column labels"
    )


if __name__ == "__main__":
    main()
