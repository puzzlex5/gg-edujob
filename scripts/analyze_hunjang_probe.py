#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SRC = Path("hunjang_probe_report.json")
OUT = Path("hunjang_api_contract_report.json")
DETAIL_KEYS = {"encRcrtNo", "conmId", "rcrtNo", "recruitNo", "recruitId", "id"}


def main() -> int:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    api_rows = []
    seen = set()
    for item in data.get("apiCandidates") or []:
        url = str(item.get("url") or "")
        if "/hunApi/" not in url:
            continue
        method = str(item.get("method") or "RESPONSE")
        parsed = urlparse(url)
        key = (method, parsed.path, str(item.get("postData") or "")[:3000])
        if key in seen:
            continue
        seen.add(key)
        api_rows.append({
            "method": method,
            "path": parsed.path,
            "query": parsed.query,
            "postData": str(item.get("postData") or "")[:3000],
            "status": item.get("status"),
            "contentType": item.get("contentType"),
            "bodySample": str(item.get("bodySample") or "")[:6000],
        })

    ids = []
    url_texts = []
    for item in data.get("idEvidence") or []:
        url_texts.append(str(item.get("url") or ""))
        if item.get("key") and item.get("value"):
            ids.append({"key": str(item["key"]), "value": str(item["value"]), "url": str(item.get("url") or "")})
    for blob in data.get("routeEvidence") or []:
        text = str(blob or "")
        for m in re.findall(r"https?://[^\s\"']+|/[^\s\"']*recruitViewDetailMain[^\s\"']*", text, flags=re.I):
            url_texts.append(m)
    for u in url_texts:
        try:
            q = parse_qs(urlparse(u).query)
        except Exception:
            continue
        for k, vals in q.items():
            if k in DETAIL_KEYS or re.search(r"(?:rcrt|recruit|conm).*?(?:no|id)?$", k, re.I):
                for v in vals:
                    if v:
                        ids.append({"key": k, "value": v[:500], "url": u[:1500]})

    unique_ids = []
    id_seen = set()
    for row in ids:
        k = (row["key"], row["value"])
        if k not in id_seen:
            id_seen.add(k)
            unique_ids.append(row)

    list_like = [r for r in api_rows if re.search(r"list|search|recruit.*(?:list|main)|(?:list|search).*recruit", r["path"], re.I)]
    detail_like = [r for r in api_rows if re.search(r"detail|view", r["path"], re.I)]
    report = {
        "source": "훈장마을",
        "generatedAt": data.get("generatedAt"),
        "publiclyReachable": data.get("publiclyReachable"),
        "probeOnly": True,
        "publicationEnabled": False,
        "hunApiCandidateCount": len(api_rows),
        "listLikeApiCandidates": list_like[:100],
        "detailLikeApiCandidates": detail_like[:100],
        "allHunApiCandidates": api_rows[:300],
        "stableIdEvidence": unique_ids[:200],
        "routeVisits": data.get("routeVisits") or [],
        "readyForTraversal": bool(list_like and unique_ids),
        "nextGate": "confirm listing request pagination/region/current-state contract and stable detail identity, then prove complete Seoul/Gyeonggi traversal",
        "safety": data.get("safety") or {},
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "hunApiCandidateCount": report["hunApiCandidateCount"],
        "listLikeCount": len(list_like),
        "detailLikeCount": len(detail_like),
        "stableIdEvidenceCount": len(unique_ids),
        "readyForTraversal": report["readyForTraversal"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
