#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from direct_post_links import is_direct_post, repair_and_mark

CULTURE_FOUNDATION_RE = re.compile(r"문화\s*재단|문화재단", re.I)


def load_payload(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else data.get("jobs", [])
    return data, rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="unified_jobs.next.json")
    ap.add_argument("--output", default="direct_post_link_report.json")
    ap.add_argument("--repair-in-place", action="store_true")
    ap.add_argument("--strict-clickable", action="store_true", help="Fail if a row marked clickable is not an exact detail URL")
    args = ap.parse_args()

    path = Path(args.input)
    data, rows = load_payload(path)
    repaired_rows = []
    before_bad = []
    after_bad = []
    culture = []
    recovered = []

    for original in rows:
        before_ok, before_reason = is_direct_post(original)
        if not before_ok:
            before_bad.append({
                "sourceIdentity": original.get("sourceIdentity"), "source": original.get("source"),
                "title": original.get("title"), "url": original.get("url") or original.get("originalUrl") or "",
                "reason": before_reason,
            })
        row = repair_and_mark(original)
        if row.get("url") != original.get("url"):
            recovered.append({
                "sourceIdentity": row.get("sourceIdentity"), "source": row.get("source"),
                "from": original.get("url") or original.get("originalUrl") or "", "to": row.get("url"),
                "reason": row.get("detailLinkReason"),
            })
        ok, reason = is_direct_post(row)
        item = {
            "sourceIdentity": row.get("sourceIdentity"), "source": row.get("source"),
            "title": row.get("title"), "school": row.get("school"),
            "url": row.get("url") or row.get("originalUrl") or "", "reason": reason,
            "direct": bool(ok),
        }
        if not ok:
            after_bad.append(item)
        hay = f"{row.get('title') or ''} {row.get('school') or ''}"
        if CULTURE_FOUNDATION_RE.search(hay):
            culture.append(item)
        repaired_rows.append(row)

    if args.repair_in_place:
        if isinstance(data, list):
            out = repaired_rows
        else:
            out = dict(data)
            out["jobs"] = repaired_rows
        path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    culture_bad = [x for x in culture if not x["direct"]]
    report = {
        "policy": "search-result-card-exact-detail-or-nonclickable-v2",
        "totalRows": len(rows),
        "nonDirectBeforeRepair": len(before_bad),
        "recoveredRows": len(recovered),
        "nonDirectAfterRepair": len(after_bad),
        "cultureFoundationRows": len(culture),
        "cultureFoundationNonDirectRows": len(culture_bad),
        "recoveredExamples": recovered[:100],
        "nonDirectExamples": after_bad[:100],
        "cultureFoundationExamples": culture[:100],
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # Remaining non-direct rows are allowed only because repair_and_mark explicitly makes
    # them non-clickable in the UI. A false-positive clickable row is a publication failure.
    if args.strict_clickable:
        bad_clickable = [r for r in repaired_rows if r.get("detailLinkVerified") is not False and not is_direct_post(r)[0]]
        if bad_clickable:
            print(f"unsafe clickable rows: {len(bad_clickable)}")
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
