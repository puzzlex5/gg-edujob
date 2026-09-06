#!/usr/bin/env python3
from __future__ import annotations

import json, re, sys
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://mcfamily.or.kr/jobs/opening"
OUT = Path("hanultari_probe_report.json")
EDU = re.compile(r"(강사|교사|교원|교육|방과후|늘봄|이중언어|한국어|교실|학교)")
DETAIL = re.compile(r"^/jobs/opening/(\d+)$")
MAX_PAGES = 300

s = requests.Session()
s.headers.update({"User-Agent": "gg-edujob-public-source-probe/1.1"})

report = {
    "source": "서울시 다문화가족 정보포털 한울타리",
    "base": BASE,
    "pages": [],
    "detailIds": [],
    "educationCandidates": [],
    "currentEducationCandidates": [],
    "detailValidation": [],
    "errors": [],
    "stopReason": None,
}
seen = set()
seen_page_signatures = set()

for page in range(1, MAX_PAGES + 1):
    try:
        r = s.get(BASE, params={"page": page}, timeout=20)
        r.raise_for_status()
    except Exception as e:
        report["errors"].append({"page": page, "error": str(e)})
        report["stopReason"] = "request-error"
        break
    soup = BeautifulSoup(r.text, "html.parser")
    ids = []
    page_candidates = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        p = urlparse(urljoin(BASE, href))
        if p.netloc != "mcfamily.or.kr":
            continue
        m = DETAIL.match(p.path)
        if not m:
            continue
        sid = m.group(1)
        ids.append(sid)
        if sid in seen:
            continue
        seen.add(sid)
        text = " ".join(a.stripped_strings)
        if EDU.search(text):
            row = {"id": sid, "title": text, "url": f"https://mcfamily.or.kr/jobs/opening/{sid}"}
            report["educationCandidates"].append(row)
            page_candidates.append(row)
            if "마감" not in text:
                report["currentEducationCandidates"].append(row)
    uniq = sorted(set(ids), key=int, reverse=True)
    signature = tuple(uniq)
    report["pages"].append({"page": page, "status": r.status_code, "ids": uniq})
    if not uniq:
        report["stopReason"] = "empty-page"
        break
    if signature in seen_page_signatures:
        report["stopReason"] = "repeated-page"
        break
    seen_page_signatures.add(signature)
else:
    report["stopReason"] = "max-pages"

# Validate every currently-open education candidate against its individual detail page.
validated_current = []
for row in report["currentEducationCandidates"]:
    try:
        d = s.get(row["url"], timeout=20)
        d.raise_for_status()
        dsoup = BeautifulSoup(d.text, "html.parser")
        body = " ".join(dsoup.stripped_strings)
        id_preserved = row["id"] in d.url or row["id"] in body
        closed = "마감" in body and not any(k in body for k in ("접수중", "진행중", "모집중"))
        item = {
            "id": row["id"],
            "requestedUrl": row["url"],
            "finalUrl": d.url,
            "status": d.status_code,
            "idPreserved": id_preserved,
            "closedEvidence": closed,
        }
        report["detailValidation"].append(item)
        if id_preserved and not closed:
            validated_current.append(row)
    except Exception as e:
        report["detailValidation"].append({"id": row["id"], "requestedUrl": row["url"], "error": str(e)})

report["currentEducationCandidates"] = validated_current
report["detailIds"] = sorted(seen, key=int, reverse=True)
report["stableIdPattern"] = "/jobs/opening/<numeric-id>"
report["sameOriginDetailOnly"] = True
report["probeDate"] = date.today().isoformat()
report["paginationComplete"] = report["stopReason"] == "empty-page"
report["ok"] = (
    len(seen) > 0
    and not report["errors"]
    and report["paginationComplete"]
    and all(v.get("idPreserved") is True for v in report["detailValidation"] if "error" not in v)
    and not any("error" in v for v in report["detailValidation"])
)
OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: report[k] for k in ("ok", "stableIdPattern", "paginationComplete", "stopReason", "detailIds", "currentEducationCandidates", "detailValidation", "errors")}, ensure_ascii=False))
if not report["ok"]:
    sys.exit(2)
