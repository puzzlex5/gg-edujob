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
PAID_MAIN_API = "/hunApi/main/prchsSubMainRecruitList"
NON_GENERAL_API_FRAGMENTS = (
    "/hunApi/main/",
    "/hunApi/common/",
    "/logApi/",
)


def walk_recruit_rows(value, out, depth=0):
    if depth > 8:
        return
    if isinstance(value, dict):
        if "rcrtNo" in value or "encRcrtNo" in value:
            out.append({
                "rcrtNo": value.get("rcrtNo"),
                "encRcrtNo": value.get("encRcrtNo"),
                "title": value.get("pbancTtl") or value.get("rcrtTtl") or value.get("rcrtNm") or value.get("rcrtTitle") or value.get("title"),
                "company": value.get("conmNm") or value.get("acdmNm") or value.get("entNm") or value.get("companyNm"),
                "region": value.get("workRegnNm") or value.get("regionNm") or value.get("addr"),
                "endDate": value.get("rceptEndDt") or value.get("endDt") or value.get("closeDt"),
                "status": value.get("rcrtPdNm") or value.get("rcrtStatNm") or value.get("statusNm") or value.get("rcrtStatCd"),
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
    navigation_attempts = []
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
            except Exception as e:
                errors.append(f"{url}: {type(e).__name__}: {e}")

        # Discover the site's own public recruitment navigation. A route change is not
        # required: some SPA controls can keep the same URL while replacing the view and
        # issuing new XHRs, so body/API deltas are recorded as evidence as well.
        if not blocked:
            try:
                page.goto(BASE, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(4000)
                selectors = [
                    "a:has-text('채용정보')",
                    "button:has-text('채용정보')",
                    "[role='button']:has-text('채용정보')",
                    "text=채용정보",
                    "a:has-text('서울')",
                    "button:has-text('서울')",
                    "text=서울",
                    "a:has-text('경기')",
                    "button:has-text('경기')",
                    "text=경기",
                ]
                for selector in selectors:
                    loc = page.locator(selector)
                    count = min(loc.count(), 8)
                    for i in range(count):
                        el = loc.nth(i)
                        try:
                            before_url = page.url
                            before_body = page.locator("body").inner_text(timeout=3000)[:12000]
                            before_requests = len(requests)
                            txt = (el.inner_text(timeout=700) or "").strip()[:120]
                            href = el.get_attribute("href") or ""
                            if href:
                                target = urljoin(BASE, href)
                                if not urlparse(target).netloc.endswith("hunjang.com"):
                                    continue
                            el.click(timeout=3000)
                            page.wait_for_timeout(5000)
                            after_url = page.url
                            after_body = page.locator("body").inner_text(timeout=5000)[:12000]
                            api_delta = requests[before_requests:]
                            changed = after_url != before_url or after_body != before_body or bool(api_delta)
                            navigation_attempts.append({
                                "selector": selector,
                                "text": txt,
                                "href": href,
                                "before": before_url,
                                "after": after_url,
                                "bodyChanged": after_body != before_body,
                                "newApiRequests": api_delta[:40],
                            })
                            if changed:
                                pages.append({
                                    "url": after_url,
                                    "requestedUrl": f"discovered-by-click:{txt or selector}",
                                    "status": None,
                                    "title": page.title(),
                                    "bodySample": after_body[:8000],
                                    "links": page.locator("a[href]").evaluate_all(
                                        "els => els.slice(0,500).map(a => ({href:a.href, text:(a.innerText||'').trim().slice(0,120)}))"
                                    ),
                                })
                                # Reset before trying another public navigation control so one
                                # successful SPA transition does not hide the region probes.
                                page.goto(BASE, wait_until="domcontentloaded", timeout=45000)
                                page.wait_for_timeout(2500)
                                break
                        except Exception:
                            continue
            except Exception as e:
                errors.append(f"spa navigation probe: {type(e).__name__}: {e}")

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
    all_recruit_api = [x for x in api_responses if "/hunApi/" in x.get("url", "") and x.get("recruitRowCount")]
    paid_main_api = [x for x in all_recruit_api if PAID_MAIN_API in x.get("url", "")]
    general_list_api = [
        x for x in all_recruit_api
        if not any(fragment in x.get("url", "") for fragment in NON_GENERAL_API_FRAGMENTS)
    ]

    report = {
        "generatedAt": generated_at,
        "source": "훈장마을",
        "baseUrl": BASE,
        "publiclyReachable": any((p.get("status") or 0) < 400 for p in pages) and not blocked,
        "humanVerificationDetected": blocked,
        "pages": pages,
        "navigationAttempts": navigation_attempts,
        "apiRequests": requests[:500],
        "apiResponses": api_responses[:500],
        "observedRecruitApiCount": len(all_recruit_api),
        "observedPaidMainApiCount": len(paid_main_api),
        "observedGeneralListApiCount": len(general_list_api),
        "observedGeneralListApiUrls": sorted({x.get("url") for x in general_list_api if x.get("url")}),
        "observedDetailApiCount": len(detail_api),
        "stableIdCandidate": "hunjang:<rcrtNo>" if numeric_ids else None,
        "numericRcrtNoCount": len(numeric_ids),
        "numericRcrtNos": numeric_ids[:1000],
        "encRcrtNoCount": len(enc_ids),
        "metroRowCount": len(metro_rows),
        "metroRows": metro_rows[:200],
        "errors": errors,
        "readyForPaginationProbe": bool(numeric_ids and general_list_api and not blocked),
        "nextGate": "identify a non-main public general recruitment list request, then prove pagination changes IDs and exhaustively traverse before writing any canonical dataset",
        "safety": {
            "tlsVerificationDisabled": False,
            "authenticationBypassed": False,
            "captchaBypassed": False,
            "publicationAttempted": False,
        },
    }
    Path("hunjang_probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("generatedAt", "publiclyReachable", "humanVerificationDetected", "numericRcrtNoCount", "observedPaidMainApiCount", "observedGeneralListApiCount", "observedGeneralListApiUrls", "readyForPaginationProbe", "nextGate")}, ensure_ascii=False))
    return 0 if report["publiclyReachable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
