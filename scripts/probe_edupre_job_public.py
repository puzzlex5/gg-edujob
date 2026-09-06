#!/usr/bin/env python3
import html as htmlmod
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://job.edupre.co.kr"
START = BASE + "/"
UA = "Mozilla/5.0 (compatible; gg-edujob-public-probe/1.0; +https://github.com/puzzlex5/gg-edujob)"
DETAIL_RE = re.compile(r'href=["\']([^"\']*recruit/view\.htm\?[^"\']*\bidx=(\d+)[^"\']*)["\']', re.I)
BLOCK_RE = re.compile(r'(captcha|recaptcha|사람인지|로봇이 아닙니다|cloudflare|접근이 제한|로그인 후 이용)', re.I)
SEOUL_GYEONGGI_RE = re.compile(r'(서울(?:특별시)?|경기(?:도)?)\s*[가-힣0-9·\- ]{0,20}')
DEADLINE_RE = re.compile(r'(채용시까지|상시채용|마감일[^<\n]{0,20}|\b20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}\b)', re.I)
TITLE_RE = re.compile(r'<title>(.*?)</title>', re.I | re.S)


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
        return r.status, r.geturl(), body


def ids_from(body):
    out = []
    seen = set()
    for m in DETAIL_RE.finditer(body):
        did = m.group(2)
        if did in seen:
            continue
        seen.add(did)
        out.append((did, urllib.parse.urljoin(BASE, htmlmod.unescape(m.group(1)))))
    return out


def title_from(body):
    m = TITLE_RE.search(body)
    if not m:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", htmlmod.unescape(m.group(1)))).strip()


def main():
    report = {
        "source": "edupre_job",
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "base": START,
        "blocked": False,
        "pages": [],
        "uniqueIds": [],
        "paginationEvidence": [],
        "detailChecks": [],
        "readyForCollector": False,
    }
    seen = set()
    page_variants = [START] + [f"{BASE}/index.htm?page={n}" for n in range(1, 6)]
    for url in page_variants:
        try:
            status, final, body = get(url)
        except Exception as e:
            report["pages"].append({"url": url, "error": type(e).__name__ + ": " + str(e)})
            continue
        blocked = bool(BLOCK_RE.search(body))
        report["blocked"] = report["blocked"] or blocked
        pairs = ids_from(body)
        ids = [x[0] for x in pairs]
        new = [x for x in ids if x not in seen]
        if seen and new:
            report["paginationEvidence"].append({"url": url, "newIds": new})
        seen.update(ids)
        report["pages"].append({
            "url": url,
            "finalUrl": final,
            "status": status,
            "bytes": len(body),
            "detailIds": ids,
            "newIds": new,
            "blocked": blocked,
            "hasSeoulGyeonggiText": bool(SEOUL_GYEONGGI_RE.search(body)),
        })

    report["uniqueIds"] = sorted(seen, key=lambda x: int(x))
    # Sample across the observed ID range, not only the oldest/newest cluster.
    ids = report["uniqueIds"]
    sample = []
    if ids:
        for idx in sorted(set([0, len(ids)//4, len(ids)//2, (3*len(ids))//4, len(ids)-1])):
            sample.append(ids[idx])
    for did in sample:
        url = f"{BASE}/recruit/view.htm?idx={did}"
        try:
            status, final, body = get(url)
            parsed = urllib.parse.urlparse(final)
            q = urllib.parse.parse_qs(parsed.query)
            report["detailChecks"].append({
                "id": did,
                "url": url,
                "status": status,
                "finalUrl": final,
                "idPreserved": q.get("idx", [None])[0] == did,
                "blocked": bool(BLOCK_RE.search(body)),
                "hasSeoulGyeonggi": bool(SEOUL_GYEONGGI_RE.search(body)),
                "hasDeadlineText": bool(DEADLINE_RE.search(body)),
                "title": title_from(body),
                "bytes": len(body),
            })
        except Exception as e:
            report["detailChecks"].append({"id": did, "url": url, "error": type(e).__name__ + ": " + str(e)})

    details_ok = bool(report["detailChecks"]) and all(
        x.get("status") == 200 and x.get("idPreserved") and not x.get("blocked")
        for x in report["detailChecks"]
    )
    distinct_page_sets = {tuple(p.get("detailIds", [])) for p in report["pages"] if p.get("detailIds")}
    report["readyForCollector"] = (
        not report["blocked"]
        and details_ok
        and len(report["uniqueIds"]) >= 20
        and len(distinct_page_sets) >= 2
        and bool(report["paginationEvidence"])
    )
    report["finishedAt"] = datetime.now(timezone.utc).isoformat()
    with open("edupre_job_probe_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["blocked"] else 0


if __name__ == "__main__":
    sys.exit(main())
