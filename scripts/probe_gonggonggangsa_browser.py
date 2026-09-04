#!/usr/bin/env python3
from __future__ import annotations

import json, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
BASE = "https://00gangsa.com"
LIST_URL = f"{BASE}/recruitments"
DETAIL_RE = re.compile(r"/recruitments/(\d+)(?:[/?#]|$)")
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한", re.I)


def main() -> int:
    now = datetime.now(KST).isoformat(timespec="seconds")
    requests = []
    responses = []
    detail_links = {}
    scripts = []
    dom_attrs = []
    errors = []
    status = None
    title = ""
    body_text = ""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ko-KR")
        page = ctx.new_page()

        def on_request(req):
            url = req.url
            if url.startswith(BASE) or "api" in url.lower():
                requests.append({
                    "method": req.method,
                    "url": url,
                    "resourceType": req.resource_type,
                    "postData": (req.post_data or "")[:2000],
                })

        def on_response(resp):
            url = resp.url
            ct = (resp.headers.get("content-type") or "").lower()
            if url.startswith(BASE) or "json" in ct or "api" in url.lower():
                item = {"status": resp.status, "url": url, "contentType": ct}
                if "json" in ct:
                    try:
                        txt = resp.text()
                        item["bodySample"] = txt[:5000]
                        for m in DETAIL_RE.finditer(txt):
                            sid = m.group(1)
                            detail_links[sid] = urljoin(BASE, f"/recruitments/{sid}")
                    except Exception as e:
                        item["bodyError"] = f"{type(e).__name__}: {e}"
                responses.append(item)

        page.on("request", on_request)
        page.on("response", on_response)
        try:
            resp = page.goto(LIST_URL, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else None
            page.wait_for_timeout(5000)
            title = page.title()
            body_text = page.locator("body").inner_text(timeout=5000)[:30000]
            if BLOCK_RE.search(body_text):
                raise RuntimeError("public page appears blocked by human-verification gate")

            for a in page.locator("a").all():
                try:
                    href = a.get_attribute("href") or ""
                    full = urljoin(BASE, href)
                    m = DETAIL_RE.search(full)
                    if m:
                        detail_links[m.group(1)] = full
                except Exception:
                    pass

            scripts = [s for s in page.locator("script[src]").evaluate_all("els => els.map(e => e.src)") if s]
            dom_attrs = page.locator("[href],[data-href],[data-url],[data-id],[data-recruitment-id]").evaluate_all(
                "els => els.slice(0,500).map(e => ({tag:e.tagName, href:e.getAttribute('href'), dataHref:e.getAttribute('data-href'), dataUrl:e.getAttribute('data-url'), dataId:e.getAttribute('data-id'), recruitmentId:e.getAttribute('data-recruitment-id'), text:(e.innerText||'').trim().slice(0,160)}))"
            )
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
        finally:
            ctx.close()
            browser.close()

    ids = sorted(detail_links, key=lambda x: int(x) if x.isdigit() else x)
    api_candidates = []
    for x in requests + responses:
        u = x.get("url", "")
        if any(k in u.lower() for k in ("api", "recruit", "job", "posting", "notice")):
            api_candidates.append(x)

    report = {
        "generatedAt": now,
        "source": "공공강사",
        "baseUrl": BASE,
        "listUrl": LIST_URL,
        "httpStatus": status,
        "pageTitle": title,
        "publiclyReachable": status == 200 and not errors,
        "stableIdPattern": "gonggonggangsa:<numeric id from /recruitments/{id}>",
        "detailIdCountInDomOrResponses": len(ids),
        "detailIds": ids[:500],
        "detailUrls": [detail_links[i] for i in ids[:500]],
        "apiCandidates": api_candidates[:300],
        "scriptUrls": scripts[:100],
        "domRouteAttributes": dom_attrs,
        "bodyTextSample": body_text[:12000],
        "errors": errors,
        "nextGate": "identify list API/pagination and prove exhaustive Seoul/Gyeonggi active traversal; do not publish before missingAfter=0",
    }
    Path("gonggonggangsa_probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("generatedAt","publiclyReachable","detailIdCountInDomOrResponses","errors","nextGate")}, ensure_ascii=False))
    return 0 if report["publiclyReachable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
