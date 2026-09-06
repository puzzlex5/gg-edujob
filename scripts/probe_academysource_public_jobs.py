#!/usr/bin/env python3
import json, re, sys, urllib.parse, urllib.request
from datetime import datetime, timezone

BASE = "https://www.academysource.kr"
START = BASE + "/Job/"
UA = "Mozilla/5.0 (compatible; gg-edujob-public-probe/1.0; +https://github.com/puzzlex5/gg-edujob)"
DETAIL_RE = re.compile(r'href=["\'](?:https?://www\.academysource\.kr)?(/Job/detail/(\d+))["\']', re.I)
REGION_RE = re.compile(r'(서울|경기)\s*([가-힣]+시|[가-힣]+구|전체)?')
BLOCK_RE = re.compile(r'(captcha|recaptcha|사람인지|로봇이 아닙니다|cloudflare|접근이 제한)', re.I)


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
        return r.status, r.geturl(), body


def ids_from(html):
    return [(m.group(2), urllib.parse.urljoin(BASE, m.group(1))) for m in DETAIL_RE.finditer(html)]


def main():
    report = {
        "source": "academysource",
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "base": START,
        "blocked": False,
        "pages": [],
        "uniqueIds": [],
        "detailChecks": [],
        "paginationEvidence": [],
        "readyForCollector": False,
    }
    seen = set()
    page_variants = [
        START,
        START + "?page=2",
        START + "?page=3",
        BASE + "/Job/index/2",
        BASE + "/Job/index/3",
    ]
    for url in page_variants:
        try:
            status, final, html = get(url)
        except Exception as e:
            report["pages"].append({"url": url, "error": type(e).__name__ + ": " + str(e)})
            continue
        blocked = bool(BLOCK_RE.search(html))
        report["blocked"] = report["blocked"] or blocked
        pairs = ids_from(html)
        ids = [x[0] for x in pairs]
        report["pages"].append({"url": url, "finalUrl": final, "status": status, "bytes": len(html), "detailIds": ids, "blocked": blocked})
        new = [x for x in ids if x not in seen]
        if seen and new:
            report["paginationEvidence"].append({"url": url, "newIds": new})
        seen.update(ids)

    report["uniqueIds"] = sorted(seen)
    # Check several exact detail URLs for persistence, same ID, region text, and no auth/CAPTCHA redirect.
    for did in sorted(seen)[:8]:
        url = f"{BASE}/Job/detail/{did}"
        try:
            status, final, html = get(url)
            report["detailChecks"].append({
                "id": did,
                "url": url,
                "status": status,
                "finalUrl": final,
                "idPreserved": final.rstrip("/").endswith("/" + did),
                "blocked": bool(BLOCK_RE.search(html)),
                "hasSeoulGyeonggi": bool(REGION_RE.search(html)),
                "bytes": len(html),
            })
        except Exception as e:
            report["detailChecks"].append({"id": did, "url": url, "error": type(e).__name__ + ": " + str(e)})

    details_ok = bool(report["detailChecks"]) and all(
        x.get("status") == 200 and x.get("idPreserved") and not x.get("blocked")
        for x in report["detailChecks"]
    )
    report["readyForCollector"] = (not report["blocked"] and details_ok and bool(report["paginationEvidence"]))
    report["finishedAt"] = datetime.now(timezone.utc).isoformat()
    with open("academysource_probe_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # Probe itself succeeds unless access is blocked; readiness is a data result, not a CI failure.
    return 2 if report["blocked"] else 0

if __name__ == "__main__":
    sys.exit(main())
