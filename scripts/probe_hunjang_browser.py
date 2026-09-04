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
HOME_URL = f"{BASE}/"
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한|access denied", re.I)
RECRUIT_RE = re.compile(r"recruit|채용|구인|job", re.I)
ID_PARAM_RE = re.compile(r"(?:rcrt|recruit|job|notice|post|idx|no|id)", re.I)


def same_origin(url: str) -> bool:
    try:
        return urlparse(url).netloc == urlparse(BASE).netloc
    except Exception:
        return False


def main() -> int:
    now = datetime.now(KST).isoformat(timespec="seconds")
    errors: list[str] = []
    pages: list[dict] = []
    requests: list[dict] = []
    responses: list[dict] = []
    candidate_links: dict[str, str] = {}
    id_evidence: list[dict] = []
    status = None

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
                    "postData": (req.post_data or "")[:3000],
                    "headers": {k: v for k, v in req.headers.items() if k.lower() in {"accept", "content-type", "origin", "referer"}},
                })

        def on_response(resp):
            u = resp.url
            ct = (resp.headers.get("content-type") or "").lower()
            if "hunjang.com" in u:
                item = {"status": resp.status, "url": u, "contentType": ct}
                if "json" in ct:
                    try:
                        item["bodySample"] = resp.text()[:5000]
                    except Exception as e:
                        item["bodyError"] = f"{type(e).__name__}: {e}"
                responses.append(item)

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            resp = page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else None
            page.wait_for_timeout(5000)
            body = page.locator("body").inner_text(timeout=5000)[:30000]
            if BLOCK_RE.search(body):
                raise RuntimeError("public page appears blocked by human-verification gate")

            anchors = page.locator("a[href]").evaluate_all(
                "els => els.slice(0,2000).map(a => ({href:a.href, text:(a.innerText||'').trim().slice(0,180)}))"
            )
            for a in anchors:
                href = a.get("href") or ""
                text = a.get("text") or ""
                if href and same_origin(href) and RECRUIT_RE.search(href + " " + text):
                    candidate_links[href] = text

            pages.append({
                "url": page.url,
                "status": status,
                "title": page.title(),
                "bodySample": body[:12000],
                "candidateLinkCount": len(candidate_links),
                "candidateLinks": [{"url": u, "text": t} for u, t in list(candidate_links.items())[:200]],
                "scriptUrls": page.locator("script[src]").evaluate_all("els => els.map(e => e.src).filter(Boolean).slice(0,200)"),
            })

            # Visit a few public same-origin recruitment candidates only. No login, form submission,
            # CAPTCHA handling, TLS bypass, or authenticated request replay is attempted.
            for target in list(candidate_links)[:8]:
                try:
                    r = page.goto(target, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2500)
                    txt = page.locator("body").inner_text(timeout=5000)[:20000]
                    if BLOCK_RE.search(txt):
                        errors.append(f"blocked candidate page: {target}")
                        continue
                    current = page.url
                    anchors2 = page.locator("a[href]").evaluate_all(
                        "els => els.slice(0,1200).map(a => ({href:a.href, text:(a.innerText||'').trim().slice(0,160)}))"
                    )
                    likely_detail = []
                    for a in anchors2:
                        href = a.get("href") or ""
                        text = a.get("text") or ""
                        if not href or not same_origin(href):
                            continue
                        if RECRUIT_RE.search(href + " " + text):
                            likely_detail.append({"url": href, "text": text})
                            parsed = urlparse(href)
                            for pair in (parsed.query or "").split("&"):
                                if "=" not in pair:
                                    continue
                                key, value = pair.split("=", 1)
                                if value and ID_PARAM_RE.search(key):
                                    id_evidence.append({"url": href, "key": key, "value": value[:240]})
                    pages.append({
                        "url": current,
                        "status": r.status if r else None,
                        "title": page.title(),
                        "bodySample": txt[:10000],
                        "candidateDetailLinks": likely_detail[:300],
                    })
                except Exception as e:
                    errors.append(f"{target}: {type(e).__name__}: {e}")
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
        finally:
            ctx.close()
            browser.close()

    api_candidates = []
    for item in requests + responses:
        u = item.get("url", "")
        low = u.lower()
        if any(k in low for k in ("api", "recruit", "job", "posting", "notice", "search")):
            api_candidates.append(item)

    report = {
        "generatedAt": now,
        "source": "훈장마을",
        "baseUrl": BASE,
        "homeUrl": HOME_URL,
        "httpStatus": status,
        "publiclyReachable": status == 200 and not any("human-verification" in x for x in errors),
        "probeOnly": True,
        "publicationEnabled": False,
        "candidatePageCount": len(pages),
        "pages": pages,
        "idEvidence": id_evidence[:500],
        "apiCandidates": api_candidates[:500],
        "errors": errors,
        "readyForTraversal": False,
        "nextGate": "identify public recruitment list/detail contract and stable ID or persistent original URL before any crawler or unified-search integration",
        "safety": {
            "captchaBypass": False,
            "tlsVerificationDisabled": False,
            "authenticationBypass": False,
            "writesToSource": False,
        },
    }
    Path("hunjang_probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "generatedAt": report["generatedAt"],
        "publiclyReachable": report["publiclyReachable"],
        "candidatePageCount": report["candidatePageCount"],
        "idEvidenceCount": len(report["idEvidence"]),
        "apiCandidateCount": len(report["apiCandidates"]),
        "errors": report["errors"][:10],
        "nextGate": report["nextGate"],
    }, ensure_ascii=False))
    return 0 if report["publiclyReachable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
