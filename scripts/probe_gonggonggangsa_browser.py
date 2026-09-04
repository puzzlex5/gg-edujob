#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
BASE = "https://00gangsa.com"
LIST_URL = f"{BASE}/recruitments"
RECRUIT_API = "https://prod-backend.00gangsa.com/v1/recruitments"
BACKEND_HOST = "prod-backend.00gangsa.com"
DETAIL_RE = re.compile(r"/recruitments/(\d+)(?:[/?#]|$)")
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한", re.I)


def summarize_payload(payload):
    if not isinstance(payload, dict):
        return {"valid": False}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    rows = data.get("list") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {"valid": False, "keys": sorted(data)[:50] if isinstance(data, dict) else []}
    ids = [str(x.get("id")) for x in rows if isinstance(x, dict) and str(x.get("id", "")).isdigit()]
    regions = []
    states = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        states.append(str(row.get("state") or ""))
        for reg in row.get("regions") or []:
            if not isinstance(reg, dict):
                continue
            province = reg.get("province") or {}
            city = reg.get("city") or {}
            regions.append({
                "provinceId": province.get("id"),
                "province": province.get("name"),
                "cityId": city.get("id"),
                "city": city.get("name"),
            })
    return {
        "valid": True,
        "count": data.get("count"),
        "totalPage": data.get("totalPage"),
        "currentPage": data.get("currentPage"),
        "hasPrevPage": data.get("hasPrevPage"),
        "hasNextPage": data.get("hasNextPage"),
        "listCount": len(rows),
        "ids": ids,
        "states": sorted(set(states)),
        "regions": regions[:100],
    }


def main() -> int:
    now = datetime.now(KST).isoformat(timespec="seconds")
    requests = []
    responses = []
    detail_links = {}
    scripts = []
    dom_attrs = []
    browser_post_tests = []
    errors = []
    status = None
    title = ""
    body_text = ""
    recruitment_payload = None
    recruitment_request_body = None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ko-KR")
        page = ctx.new_page()

        def on_request(req):
            nonlocal recruitment_request_body
            url = req.url
            if url.startswith(BASE) or BACKEND_HOST in url or "api" in url.lower():
                item = {
                    "method": req.method,
                    "url": url,
                    "resourceType": req.resource_type,
                    "postData": (req.post_data or "")[:4000],
                    "headers": {k: v for k, v in req.headers.items() if k.lower() in {"accept", "content-type", "origin", "referer", "user-agent"}},
                }
                requests.append(item)
                if url.split("?")[0] == RECRUIT_API and req.method.upper() == "POST" and recruitment_request_body is None:
                    try:
                        recruitment_request_body = json.loads(req.post_data or "{}")
                    except Exception:
                        recruitment_request_body = None

        def on_response(resp):
            nonlocal recruitment_payload
            url = resp.url
            ct = (resp.headers.get("content-type") or "").lower()
            if url.startswith(BASE) or BACKEND_HOST in url or "json" in ct or "api" in url.lower():
                item = {"status": resp.status, "url": url, "contentType": ct}
                if "json" in ct:
                    try:
                        txt = resp.text()
                        item["bodySample"] = txt[:5000]
                        payload = json.loads(txt)
                        item["summary"] = summarize_payload(payload)
                        if url.split("?")[0] == RECRUIT_API and item["summary"].get("valid") and recruitment_payload is None:
                            recruitment_payload = payload
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

            # Replay the exact public application contract: POST JSON to /v1/recruitments.
            # The prior probe incorrectly selected /v1/fields as the first valid backend payload
            # and then tested GET query parameters. Keep the request inside the verified browser
            # context; no TLS, CAPTCHA, or authentication bypass is used.
            base_body = recruitment_request_body if isinstance(recruitment_request_body, dict) else {"page": 1, "size": 10, "order": "RECENT"}
            candidates = [
                dict(base_body),
                {**base_body, "page": 2},
                {**base_body, "page": 3},
                {**base_body, "page": 1, "size": 100},
            ]
            for body in candidates:
                result = page.evaluate(
                    """async ({url, body}) => {
                      try {
                        const r = await fetch(url, {
                          method: 'POST',
                          credentials: 'include',
                          headers: {'Content-Type': 'application/json', 'Accept': 'application/json, text/plain, */*'},
                          body: JSON.stringify(body)
                        });
                        const text = await r.text();
                        let payload = null;
                        try { payload = JSON.parse(text); } catch (_) {}
                        return {url, status:r.status, contentType:r.headers.get('content-type')||'', payload, textSample:text.slice(0,1000)};
                      } catch (e) {
                        return {url, error:String(e)};
                      }
                    }""",
                    {"url": RECRUIT_API, "body": body},
                )
                summary = summarize_payload(result.get("payload")) if isinstance(result, dict) else {"valid": False}
                browser_post_tests.append({
                    "body": body,
                    "status": result.get("status") if isinstance(result, dict) else None,
                    "url": result.get("url") if isinstance(result, dict) else RECRUIT_API,
                    "error": result.get("error") if isinstance(result, dict) else "invalid result",
                    "summary": summary,
                })
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
        finally:
            ctx.close()
            browser.close()

    ids = sorted(detail_links, key=lambda x: int(x) if x.isdigit() else x)
    api_candidates = []
    for x in requests + responses:
        u = x.get("url", "")
        if BACKEND_HOST in u or any(k in u.lower() for k in ("api", "recruit", "job", "posting", "notice")):
            api_candidates.append(x)

    baseline = summarize_payload(recruitment_payload) if recruitment_payload else {"valid": False}
    baseline_ids = baseline.get("ids") or []
    page_param_evidence = []
    size_param_evidence = []
    for t in browser_post_tests:
        s = t.get("summary") or {}
        body = t.get("body") or {}
        if not s.get("valid"):
            continue
        if body.get("page") in (2, 3) and (s.get("currentPage") == body.get("page") or (s.get("ids") and s.get("ids") != baseline_ids)):
            page_param_evidence.append(t)
        if body.get("size") == 100 and s.get("listCount", 0) > len(baseline_ids):
            size_param_evidence.append(t)

    observed_regions = []
    for t in browser_post_tests:
        s = t.get("summary") or {}
        observed_regions.extend(s.get("regions") or [])
    metro_evidence = [r for r in observed_regions if r.get("province") in {"서울", "경기"}]

    report = {
        "generatedAt": now,
        "source": "공공강사",
        "baseUrl": BASE,
        "listUrl": LIST_URL,
        "httpStatus": status,
        "pageTitle": title,
        "publiclyReachable": status == 200 and not errors,
        "stableIdPattern": "gonggonggangsa:<numeric id from /recruitments/{id}>",
        "recruitmentApi": RECRUIT_API,
        "recruitmentMethod": "POST",
        "observedRequestBody": recruitment_request_body,
        "recruitmentSummary": baseline,
        "detailIdCountInDomOrResponses": len(ids),
        "detailIds": ids[:500],
        "detailUrls": [detail_links[i] for i in ids[:500]],
        "browserPostTests": browser_post_tests,
        "pageParamEvidence": page_param_evidence,
        "sizeParamEvidence": size_param_evidence,
        "metroEvidence": metro_evidence[:100],
        "apiCandidates": api_candidates[:300],
        "scriptUrls": scripts[:100],
        "domRouteAttributes": dom_attrs,
        "bodyTextSample": body_text[:12000],
        "errors": errors,
        "readyForTraversal": bool(baseline.get("valid") and page_param_evidence),
        "recommendedTraversal": {
            "method": "POST",
            "bodyTemplate": {**(recruitment_request_body or {"page": 1, "size": 10, "order": "RECENT"}), "page": "<n>", "size": 100},
            "localFilter": "state == RECRUITING and any regions[].province.name in {서울, 경기}",
        },
        "nextGate": "prove exhaustive Seoul/Gyeonggi active traversal and missingAfter=0; do not publish until candidate validator passes",
    }
    Path("gonggonggangsa_probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("generatedAt", "publiclyReachable", "recruitmentApi", "readyForTraversal", "errors", "nextGate")}, ensure_ascii=False))
    return 0 if report["publiclyReachable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
