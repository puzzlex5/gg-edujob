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

s = requests.Session()
s.headers.update({"User-Agent": "gg-edujob-public-source-probe/1.0"})

report = {"source":"서울시 다문화가족 정보포털 한울타리","base":BASE,"pages":[],"detailIds":[],"educationCandidates":[],"errors":[]}
seen = set()

for page in range(1, 8):
    try:
        r = s.get(BASE, params={"page": page}, timeout=20)
        r.raise_for_status()
    except Exception as e:
        report["errors"].append({"page":page,"error":str(e)})
        break
    soup = BeautifulSoup(r.text, "html.parser")
    ids = []
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
            report["educationCandidates"].append({"id":sid,"title":text,"url":f"https://mcfamily.or.kr/jobs/opening/{sid}"})
    uniq = sorted(set(ids), key=int, reverse=True)
    report["pages"].append({"page":page,"status":r.status_code,"ids":uniq})
    if page > 1 and not uniq:
        break

report["detailIds"] = sorted(seen, key=int, reverse=True)
report["stableIdPattern"] = "/jobs/opening/<numeric-id>"
report["sameOriginDetailOnly"] = True
report["probeDate"] = date.today().isoformat()
report["ok"] = len(seen) > 0 and not report["errors"]
OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k:report[k] for k in ("ok","stableIdPattern","detailIds","educationCandidates","errors")}, ensure_ascii=False))
if not report["ok"]:
    sys.exit(2)
