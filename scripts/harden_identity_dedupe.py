#!/usr/bin/env python3
"""Make support-office identities stable and deduplication conservative.

False-positive deduplication is worse than a temporary duplicate for a recruitment
aggregator: it silently removes a real posting.  This hardener therefore only
semantically merges rows when province + normalized title + normalized school +
registration date all agree.  Rows without enough identity evidence remain
separate.  Gyeonggi support-office IDs are also changed from Python's randomized
hash() to a deterministic SHA-1-derived key.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/scrape_jobs.py"
s = p.read_text(encoding="utf-8")

if "import hashlib\n" not in s:
    s = s.replace("import json\n", "import json\nimport hashlib\n", 1)

s = s.replace(
    '"id":"goe-office-"+str(abs(hash(detail)))',
    '"id":"goe-office-"+hashlib.sha1(detail.encode("utf-8")).hexdigest()[:20]',
)

start = s.find("def dedupe_merge(jobs):\n")
end = s.find("\ndef main():\n", start)
if start < 0 or end < 0:
    raise SystemExit("Cannot locate dedupe_merge function")

new_func = '''def dedupe_merge(jobs):
    """Conservatively merge only strongly-identical announcements.

    Never merge different schools just because they use the same generic title.
    If school or registration date is missing, retain the row unless its exact
    source identity is duplicated.
    """
    jobs = sorted(jobs, key=lambda j: 0 if j.get("sourceType") == "통합게시판" else 1)
    by_key = {}
    out = []
    for j in jobs:
        titlekey = norm(j.get("title", ""))
        if not titlekey:
            continue
        province = clean(j.get("province", ""))
        schoolkey = norm(j.get("school", ""))
        registered = clean(j.get("registered", ""))

        # Strong semantic identity is intentionally strict.  Generic recruitment
        # titles are reused by many schools, so province+title alone is unsafe.
        if schoolkey and registered:
            key = "semantic|" + "|".join((province, titlekey, schoolkey, registered))
        else:
            exact_id = clean(j.get("id", ""))
            exact_url = clean(j.get("url", ""))
            key = "exact|" + "|".join((province, exact_id, exact_url))

        if key not in by_key:
            j["checkedSources"] = list(dict.fromkeys(j.get("checkedSources") or [j.get("source", "")]))
            by_key[key] = j
            out.append(j)
            continue

        old = by_key[key]
        sources = list(dict.fromkeys(
            (old.get("checkedSources") or [old.get("source", "")]) +
            (j.get("checkedSources") or [j.get("source", "")])
        ))
        old["checkedSources"] = [x for x in sources if x]
        if len(old["checkedSources"]) > 1:
            old["source"] = " + ".join(old["checkedSources"][:3]) + (
                f" 외 {len(old['checkedSources'])-3}곳" if len(old["checkedSources"]) > 3 else ""
            )
        for field in ("region", "school", "applyEnd", "applyStart", "workStart", "workEnd", "subject"):
            if not old.get(field) and j.get(field):
                old[field] = j[field]
        old["regions"] = list(dict.fromkeys((old.get("regions") or []) + (j.get("regions") or [])))
    return out
'''

s = s[:start] + new_func + s[end:]

required = [
    'hashlib.sha1(detail.encode("utf-8")).hexdigest()[:20]',
    'key = "semantic|" + "|".join((province, titlekey, schoolkey, registered))',
    'key = "exact|" + "|".join((province, exact_id, exact_url))',
]
for marker in required:
    if marker not in s:
        raise SystemExit(f"Identity hardening missing: {marker}")
if 'province+"|"+titlekey if len(titlekey)>=14' in s:
    raise SystemExit("Unsafe province+title-only dedupe remains")

p.write_text(s, encoding="utf-8")
print("Stable posting identity and conservative dedupe ready")
