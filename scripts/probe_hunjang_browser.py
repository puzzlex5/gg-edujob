#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
BASE = "https://www.hunjang.com"
HOME_URL = f"{BASE}/"
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한|access denied", re.I)
RECRUIT_RE = re.compile(r"recruit|채용|구인|job|rcrt", re.I)
ID_PARAM_RE = re.compile(r"(?:rcrt|recruit|job|notice|post|idx|no|id)", re.I)
ROUTE_RE = re.compile(r"/[A-Za-z0-9_./?=&%-]*(?:recruit|rcrt|job)[A-Za-z0-9_./?=&%-]*", re.I)
API_RE = re.compile(r"https?://[^\"'`\\\s]+|/[A-Za-z0-9_./?=&%-]*(?:api|recruit|rcrt|job|search)[A-Za-z0-9_./?=&%-]*", re.I)


def same_origin(url: str) -> bool:
    try:
        return urlparse(url).netloc == urlparse(BASE).netloc
    except Exception:
        return False


def main() -> int:
    now = datetime.now(KST).isoformat(timespec="seconds")
    errors: list[str] = []
    requests: list[dict] = []
    responses: list[dict] = []
    id_evidence: list[dict] = []
    script_evidence: list[dict] = []
    route_evidence: list[str] = []
    api_text_evidence: list[str] = []
    storage_snapshot: dict = {}
    status = None
    title = ""
    body = ""
    scripts: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ko-KR")
        page = ctx.new_page()

        def on_request(req):
            u = req.url
            if "hunjang.com" in u:
                requests.append({
                    "method": req.method,
                    "url": u,
                    "resourceType": req.resource_type,
                    "postData": (req.post_data or "")[:5000],
                    "headers": {k: v for k, v in req.headers.items() if k.lower() in {"accept", "content-type", "origin", "referer"}},
                })

        def on_response(resp):
            u = resp.url
            ct = (resp.headers.get("content-type") or "").lower()
            if "hunjang.com" in u:
                item = {"status": resp.status, "url": u, "contentType": ct}
                if "json" in ct:
                    try:
                        item["bodySample"] = resp.text()[:10000]
                    except Exception as e:
                        item["bodyError"] = f"{type(e).__name__}: {e}"
                responses.append(item)

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            resp = page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else None
            page.wait_for_timeout(8000)
            title = page.title()
            body = page.locator("body").inner_text(timeout=5000)[:40000]
            if BLOCK_RE.search(body):
                raise RuntimeError("public page appears blocked by human-verification gate")

            scripts = page.locator("script[src]").evaluate_all("els => els.map(e => e.src).filter(Boolean).slice(0,300)")
            storage_snapshot = page.evaluate("""() => ({
              localStorage: Object.fromEntries(Object.entries(localStorage).slice(0,100)),
              sessionStorage: Object.fromEntries(Object.entries(sessionStorage).slice(0,100)),
              location: location.href,
              historyState: history.state
            })""")

            # Read only publicly loaded JS resources inside the verified browser context.
            for src in scripts[:80]:
                try:
                    result = page.evaluate("""async (url) => {
                      try {
                        const r = await fetch(url, {credentials:'same-origin'});
                        const t = await r.text();
                        return {status:r.status, text:t.slice(0,2000000)};
                      } catch(e) { return {error:String(e), text:''}; }
                    }""", src)
                    text = result.get("text", "") if isinstance(result, dict) else ""
                    if not text:
                        continue
                    routes = sorted(set(ROUTE_RE.findall(text)))[:500]
                    apis = sorted(set(API_RE.findall(text)))[:500]
                    interesting = [x for x in routes + apis if RECRUIT_RE.search(x)]
                    if interesting:
                        script_evidence.append({"url": src, "status": result.get("status"), "matches": interesting[:500]})
                        route_evidence.extend(routes)
                        api_text_evidence.extend(apis)
                except Exception as e:
                    errors.append(f"script {src}: {type(e).__name__}: {e}")

            # Collect current DOM/router attributes after SPA hydration.
            dom_items = page.locator("[href],[data-href],[data-url],[data-id],[onclick],[role='link']").evaluate_all(
                "els => els.slice(0,3000).map(e => ({tag:e.tagName, href:e.getAttribute('href'), dataHref:e.getAttribute('data-href'), dataUrl:e.getAttribute('data-url'), dataId:e.getAttribute('data-id'), onclick:e.getAttribute('onclick'), text:(e.innerText||'').trim().slice(0,180)}))"
            )
            for item in dom_items:
                blob = " ".join(str(item.get(k) or "") for k in ("href","dataHref","dataUrl","dataId","onclick","text"))
                if RECRUIT_RE.search(blob):
                    href = item.get("href") or item.get("dataHref") or item.get("dataUrl") or ""
                    parsed = urlparse(href) if href else None
                    if parsed:
                        for pair in (parsed.query or "").split("&"):
                            if "=" not in pair:
                                continue
                            key, value = pair.split("=", 1)
                            if value and ID_PARAM_RE.search(key):
                                id_evidence.append({"url": href, "key": key, "value": value[:240]})
                    route_evidence.append(blob[:1000])
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
        finally:
            ctx.close()
            browser.close()

    api_candidates = []
    for item in requests + responses:
        u = item.get("url", "")
        low = u.lower()
        if any(k in low for k in ("api", "recruit", "rcrt", "job", "posting", "notice", "search")):
            api_candidates.append(item)

    route_evidence = sorted(set(x for x in route_evidence if x))[:1000]
    api_text_evidence = sorted(set(x for x in api_text_evidence if x))[:1000]
    report = {
        "generatedAt": now,
        "source": "훈장마을",
        "baseUrl": BASE,
        "homeUrl": HOME_URL,
        "httpStatus": status,
        "pageTitle": title,
        "publiclyReachable": status == 200 and not any("human-verification" in x for x in errors),
        "probeOnly": True,
        "publicationEnabled": False,
        "bodySample": body[:12000],
        "scriptUrls": scripts,
        "scriptEvidence": script_evidence,
        "routeEvidence": route_evidence,
        "apiTextEvidence": api_text_evidence,
        "idEvidence": id_evidence[:500],
        "apiCandidates": api_candidates[:1000],
        "storageSnapshot": storage_snapshot,
        "errors": errors,
        "readyForTraversal": False,
        "nextGate": "identify public recruitment list/detail contract and stable ID or persistent original URL before any crawler or unified-search integration",
        "safety": {"captchaBypass": False, "tlsVerificationDisabled": False, "authenticationBypass": False, "writesToSource": False},
    }
    Path("hunjang_probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "generatedAt": report["generatedAt"], "publiclyReachable": report["publiclyReachable"],
        "scriptEvidenceCount": len(script_evidence), "routeEvidenceCount": len(route_evidence),
        "idEvidenceCount": len(report["idEvidence"]), "apiCandidateCount": len(report["apiCandidates"]),
        "errors": report["errors"][:10], "nextGate": report["nextGate"]
    }, ensure_ascii=False))
    return 0 if report["publiclyReachable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
