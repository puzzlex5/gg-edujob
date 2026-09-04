#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
BASE = "https://www.hunjang.com"
LIST_CANDIDATES = [
    f"{BASE}/",
    f"{BASE}/recruit",
    f"{BASE}/recruit/recruitList",
]
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한|보안문자", re.I)


def walk_recruit_rows(value, out, depth=0):
    if depth > 8:
        return
    if isinstance(value, dict):
        if "rcrtNo" in value or "encRcrtNo" in value:
            out.append({
                "rcrtNo": value.get("rcrtNo"),
                "encRcrtNo": value.get("encRcrtNo"),
                "title": value.get("rcrtNm") or value.get("rcrtTitle") or value.get("title"),
                "company": value.get("acdmNm") or value.get("entNm") or value.get("companyNm"),
                "region": value.get("workRegnNm") or value.get("regionNm") or value.get("addr"),
                "endDate": value.get("rceptEndDt") or value.get("endDt") or value.get("closeDt"),
                "status": value.get("rcrtStatNm") or value.get("statusNm") or value.get("rcrtStatCd"),
            })
        for v in value.values():
            walk_recruit_rows(v, out, depth + 1)
    elif isinstance(value, list):
        for v in value:
            walk_recruit_rows(v, out, depth + 1)


def main() -> int:
    generated_at = datetime.now(KST).isoformat(timespec="seconds")
    pages = []
    api_responses = []
    recruit_rows = []
    requests = []
    errors = []
    blocked = False

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ko-KR")
        page = ctx.new_page()

        def on_request(req):
            if "/hunApi/" in req.url or "recruit" in req.url.lower():
                requests.append({
                    "method": req.method,
                    "url": req.url,
                    "resourceType": req.resource_type,
                    "postData": (req.post_data or "")[:6000],
                    "headers": {k: v for k, v in req.headers.items() if k.lower() in {"accept", "content-type", "origin", "referer"}},
                })

        def on_response(resp):
            url = resp.url
            ct = (resp.headers.get("content-type") or "").lower()
            if "/hunApi/" not in url and "json" not in ct:
                return
            item = {"url": url, "status": resp.status, "contentType": ct}
            try:
                text = resp.text()
                item["bodySample"] = text[:5000]
                if "json" in ct or text.lstrip().startswith(("{", "[")):
                    payload = json.loads(text)
                    found = []
                    walk_recruit_rows(payload, found)
                    if found:
                        item["recruitRowCount"] = len(found)
                        item["recruitRows"] = found[:100]
                        recruit_rows.extend(found)
            except Exception as e:
                item["bodyError"] = f"{type(e).__name__}: {e}"
            api_responses.append(item)

        page.on("request", on_request)
        page.on("response", on_response)

        for url in LIST_CANDIDATES:
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(5000)
                body = page.locator("body").inner_text(timeout=7000)[:25000]
                title = page.title()
                status = resp.status if resp else None
                if BLOCK_RE.search(body):
                    blocked = True
                pages.append({
                    "url": page.url,
                    "requestedUrl": url,
                    "status": status,
                    "title": title,
                    "bodySample": body[:8000],
                    "links": page.locator("a[href]").evaluate_all(
                        "els => els.slice(0,500).map(a => ({href:a.href, text:(a.innerText||'').trim().slice(0,120)}))"
                    ),
                })
                if recruit_rows:
                    break
            except Exception as e:
                errors.append(f"{url}: {type(e).__name__}: {e}")

        # If recruitment cards/buttons are visible, click at most one public navigation target
        # to expose the application's own list/detail contract. No login, CAPTCHA or auth bypass.
        if not blocked:
            try:
                candidates = page.locator("a[href*='recruit'], button").all()
                for el in candidates[:80]:
                    try:
                        txt = (el.inner_text(timeout=500) or "").strip()
                        href = el.get_attribute("href") or ""
                        if any(k in txt for k in ("채용", "구인", "강사")) or "recruit" in href.lower():
                            if href:
                                target = urljoin(BASE, href)
                                if urlparse(target).netloc.endswith("hunjang.com"):
                                    page.goto(target, wait_until="domcontentloaded", timeout=30000)
                                    page.wait_for_timeout(4000)
                                    break
                    except Exception:
                        continue
            except Exception as e:
                errors.append(f"navigation probe: {type(e).__name__}: {e}")

        ctx.close()
        browser.close()

    # Deduplicate by strong source evidence only. encRcrtNo is retained as observed opaque token;
    # rcrtNo is the preferred stable numeric source ID when present.
    dedup = {}
    for row in recruit_rows:
        rid = str(row.get("rcrtNo") or "").strip()
        enc = str(row.get("encRcrtNo") or "").strip()
        key = rid if rid else f"enc:{enc}" if enc else json.dumps(row, ensure_ascii=False, sort_keys=True)
        dedup[key] = row
    unique_rows = list(dedup.values())
    numeric_ids = sorted({str(r.get("rcrtNo")) for r in unique_rows if str(r.get("rcrtNo") or "").isdigit()}, key=int)
    enc_ids = sorted({str(r.get("encRcrtNo")) for r in unique_rows if str(r.get("encRcrtNo") or "").strip()})
    metro_rows = [r for r in unique_rows if any(x in str(r.get("region") or "") for x in ("서울", "경기"))]

    detail_api = [x for x in api_responses if "recruitDetail" in x.get("url", "")]
    list_api = [x for x in api_responses if "/hunApi/" in x.get("url", "") and x.get("recruitRowCount")]

    report = {
        "generatedAt": generated_at,
        "source": "훈장마을",
        "baseUrl": BASE,
        "publiclyReachable": any((p.get("status") or 0) < 400 for p in pages) and not blocked,
        "humanVerificationDetected": blocked,
        "pages": pages,
        "apiRequests": requests[:300],
        "apiResponses": api_responses[:300],
        "observedListApiCount": len(list_api),
        "observedDetailApiCount": len(detail_api),
        "stableIdCandidate": "hunjang:<rcrtNo>" if numeric_ids else None,
        "numericRcrtNoCount": len(numeric_ids),
        "numericRcrtNos": numeric_ids[:1000],
        "encRcrtNoCount": len(enc_ids),
        "metroRowCount": len(metro_rows),
        "metroRows": metro_rows[:200],
        "errors": errors,
        "readyForPaginationProbe": bool(numeric_ids and list_api and not blocked),
        "nextGate": "identify the exact public list request body/query, prove pagination changes IDs and exhaustively traverse before writing any canonical dataset",
        "safety": {
            "tlsVerificationDisabled": False,
            "authenticationBypassed": False,
            "captchaBypassed": False,
            "publicationAttempted": False,
        },
    }
    Path("hunjang_probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("generatedAt", "publiclyReachable", "humanVerificationDetected", "numericRcrtNoCount", "observedListApiCount", "readyForPaginationProbe", "nextGate")}, ensure_ascii=False))
    return 0 if report["publiclyReachable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
