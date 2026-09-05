#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

GENERIC_PATH_RE = re.compile(
    r"(?:/main(?:\.[a-z]+)?$|/index(?:\.[a-z]+)?$|/search(?:/|$)|/lists?(?:/|$)|"
    r"selectNttList\.do$|/FUS/JO/JOL11\.do$|/recruitments/?$)",
    re.I,
)
CULTURE_FOUNDATION_RE = re.compile(r"문화\s*재단|문화재단", re.I)


def load_rows(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("jobs", [])


def q(url: str):
    try:
        return parse_qs(urlparse(url).query)
    except Exception:
        return {}


def known_exact_identity(job: dict) -> tuple[bool, str]:
    url = str(job.get("url") or job.get("originalUrl") or "").strip()
    source = str(job.get("source") or "")
    sid = str(job.get("sourceIdentity") or "")
    params = q(url)
    path = urlparse(url).path if url else ""

    # POST-backed Seoul support-office details are exact even when the public detail
    # page needs the small open-job bridge to submit job_seq.
    if str(job.get("openMethod") or "").upper() == "POST":
        seq = str((job.get("openParams") or {}).get("job_seq") or "")
        if seq.isdigit() and job.get("openUrl"):
            return True, "seoul-post-job-seq"

    if source == "아트모아" or sid.startswith("artmore:"):
        rec = str((params.get("rec_idx") or [""])[0])
        return bool(rec.isdigit() and path.endswith("/search_view.do")), "artmore-rec-idx"

    if source == "레슨인포" or sid.startswith(("culture:id:", "board:")):
        ident = str((params.get("id") or [""])[0])
        wr_no = str((params.get("wr_no") or [""])[0])
        if path.endswith("/culture-jobs/detail.php") and ident.isdigit():
            return True, "lessoninfo-culture-id"
        if path.endswith("/board/board.php") and wr_no.isdigit():
            return True, "lessoninfo-wr-no"
        return False, "lessoninfo-generic-or-missing-id"

    if source == "잡티처" or sid.startswith("jobteacher:"):
        return bool(re.search(r"/employ/detail/\d+(?:/|$)", path)), "jobteacher-detail-id"

    if source == "공공강사" or sid.startswith("gonggonggangsa:"):
        return bool(re.search(r"/recruitments/\d+(?:/|$)", path)), "gonggonggangsa-detail-id"

    if sid.startswith("goe-central:"):
        pb = str((params.get("pbancSn") or [""])[0])
        return pb.isdigit(), "goe-pbancSn"

    if sid.startswith("seoul-central:"):
        rid = str((params.get("q_rcrtSn") or params.get("rcrtSn") or [""])[0])
        return rid.isdigit(), "seoul-central-rcrtSn"

    if sid.startswith("mircms:"):
        bbs = str((params.get("bbsId") or [job.get("bbsId") or ""])[0])
        ntt = str((params.get("nttSn") or [job.get("nttSn") or ""])[0])
        return bool(path.endswith("selectNttInfo.do") and bbs.isdigit() and ntt.isdigit()), "gyeonggi-bbs-ntt"

    if sid.startswith("seoul:"):
        seq = str((params.get("job_seq") or [(job.get("openParams") or {}).get("job_seq") or ""])[0])
        return seq.isdigit(), "seoul-job-seq"

    if sid.startswith("sen-office-cms-"):
        return bool(re.search(r"/CMS/.+/\d+_\d+\.html$", path, re.I)), "seoul-cms-detail"

    if not url:
        return False, "missing-url"
    if GENERIC_PATH_RE.search(path):
        return False, "generic-list-or-home"
    if job.get("boardUrl") and str(job.get("boardUrl")) == url:
        return False, "url-equals-board-url"
    return True, "unclassified-nongeneric-url"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="unified_jobs.json")
    ap.add_argument("--output", default="direct_post_link_report.json")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    rows = load_rows(Path(args.input))
    bad = []
    culture = []
    for job in rows:
        ok, reason = known_exact_identity(job)
        title = str(job.get("title") or "")
        school = str(job.get("school") or "")
        item = {
            "sourceIdentity": job.get("sourceIdentity"),
            "source": job.get("source"),
            "title": title,
            "school": school,
            "url": job.get("url") or job.get("originalUrl") or "",
            "reason": reason,
        }
        if not ok:
            bad.append(item)
        if CULTURE_FOUNDATION_RE.search(title + " " + school):
            culture.append({**item, "direct": ok})

    culture_bad = [x for x in culture if not x["direct"]]
    report = {
        "totalRows": len(rows),
        "nonDirectRows": len(bad),
        "cultureFoundationRows": len(culture),
        "cultureFoundationNonDirectRows": len(culture_bad),
        "nonDirectExamples": bad[:100],
        "cultureFoundationNonDirectExamples": culture_bad[:100],
        "policy": "search-result-card-must-open-exact-posting-detail-v1",
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and bad:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
