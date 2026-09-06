#!/usr/bin/env python3
import html as htmlmod
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://job.edupre.co.kr"
UA = "Mozilla/5.0 (compatible; gg-edujob-public-probe/2.0; +https://github.com/puzzlex5/gg-edujob)"
DETAIL_RE = re.compile(r'href=["\']([^"\']*recruit/view\.htm\?[^"\']*\bidx=(\d+)[^"\']*)["\']', re.I)
BLOCK_RE = re.compile(r'(captcha|recaptcha|사람인지|로봇이 아닙니다|cloudflare|접근이 제한|로그인 후 이용)', re.I)
SEOUL_RE = re.compile(r'서울(?:특별시)?')
GYEONGGI_RE = re.compile(r'경기(?:도)?')
TITLE_RE = re.compile(r'<title>(.*?)</title>', re.I | re.S)
DEADLINE_RE = re.compile(r'(채용시까지|상시채용|마감일[^<\n]{0,30}|\b20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}\b)', re.I)
MAX_PAGES = 300


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.geturl(), r.read().decode("utf-8", "replace")


def ids_from(body):
    out, seen = [], set()
    for m in DETAIL_RE.finditer(body):
        sid = m.group(2)
        if sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
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
        "blocked": False,
        "maxPages": MAX_PAGES,
        "pages": [],
        "uniqueIds": [],
        "terminationReason": None,
        "traversalComplete": False,
        "detailChecks": [],
        "readyForCollector": False,
    }
    seen_ids = set()
    seen_signatures = set()

    for page_no in range(0, MAX_PAGES + 1):
        url = BASE + "/" if page_no == 0 else f"{BASE}/index.htm?page={page_no}"
        try:
            status, final, body = get(url)
        except Exception as e:
            report["pages"].append({"page": page_no, "url": url, "error": type(e).__name__ + ": " + str(e)})
            break
        blocked = bool(BLOCK_RE.search(body))
        report["blocked"] = report["blocked"] or blocked
        ids = ids_from(body)
        sig = tuple(ids)
        new_ids = [x for x in ids if x not in seen_ids]
        row = {
            "page": page_no,
            "url": url,
            "finalUrl": final,
            "status": status,
            "bytes": len(body),
            "idCount": len(ids),
            "newIdCount": len(new_ids),
            "firstId": ids[0] if ids else None,
            "lastId": ids[-1] if ids else None,
            "blocked": blocked,
        }
        if blocked:
            row["terminationReason"] = "blocked"
            report["terminationReason"] = "blocked"
            report["pages"].append(row)
            break
        if not ids:
            row["terminationReason"] = "empty-page"
            report["terminationReason"] = "empty-page"
            report["traversalComplete"] = True
            report["pages"].append(row)
            break
        if sig in seen_signatures:
            row["terminationReason"] = "repeated-page-signature"
            report["terminationReason"] = "repeated-page-signature"
            report["traversalComplete"] = True
            report["pages"].append(row)
            break
        seen_signatures.add(sig)
        seen_ids.update(ids)
        report["pages"].append(row)
    else:
        report["terminationReason"] = "max-page-cap"

    report["uniqueIds"] = sorted(seen_ids, key=int)

    ids = report["uniqueIds"]
    sample = []
    if ids:
        for idx in sorted(set([0, len(ids)//8, len(ids)//4, len(ids)//2, (3*len(ids))//4, (7*len(ids))//8, len(ids)-1])):
            sample.append(ids[idx])
    for sid in sample:
        url = f"{BASE}/recruit/view.htm?idx={sid}"
        try:
            status, final, body = get(url)
            q = urllib.parse.parse_qs(urllib.parse.urlparse(final).query)
            report["detailChecks"].append({
                "id": sid,
                "url": url,
                "status": status,
                "finalUrl": final,
                "idPreserved": q.get("idx", [None])[0] == sid,
                "blocked": bool(BLOCK_RE.search(body)),
                "hasSeoul": bool(SEOUL_RE.search(body)),
                "hasGyeonggi": bool(GYEONGGI_RE.search(body)),
                "hasDeadlineText": bool(DEADLINE_RE.search(body)),
                "title": title_from(body),
                "bytes": len(body),
            })
        except Exception as e:
            report["detailChecks"].append({"id": sid, "url": url, "error": type(e).__name__ + ": " + str(e)})

    details_ok = bool(report["detailChecks"]) and all(
        x.get("status") == 200 and x.get("idPreserved") and not x.get("blocked")
        for x in report["detailChecks"]
    )
    growth_pages = sum(1 for p in report["pages"] if (p.get("newIdCount") or 0) > 0)
    report["readyForCollector"] = (
        report["traversalComplete"]
        and not report["blocked"]
        and details_ok
        and len(report["uniqueIds"]) >= 20
        and growth_pages >= 2
        and report["terminationReason"] in {"empty-page", "repeated-page-signature"}
    )
    report["finishedAt"] = datetime.now(timezone.utc).isoformat()
    with open("edupre_full_traversal_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["readyForCollector"] else 2


if __name__ == "__main__":
    sys.exit(main())
