#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

KST = timezone(timedelta(hours=9))
BASE = "https://www.hunjang.com"
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한|보안문자", re.I)
RECRUIT_RE = re.compile(r"recruit|rcrt|채용|강사", re.I)
EXCLUDE_RE = re.compile(r"login|signin|member|resume|mypage|academy/(?:manage|management)|recruitMng", re.I)


def same_public_origin(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme == "https" and p.netloc.endswith("hunjang.com") and not EXCLUDE_RE.search(p.path)
    except Exception:
        return False


def main() -> int:
    generated_at = datetime.now(KST).isoformat(timespec="seconds")
    route_candidates: list[dict] = []
    visits: list[dict] = []
    requests: list[dict] = []
    errors: list[str] = []
    blocked = False

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ko-KR")
        page = ctx.new_page()

        def on_request(req):
            if "/hunApi/" in req.url or RECRUIT_RE.search(req.url):
                requests.append({
                    "method": req.method,
                    "url": req.url,
                    "resourceType": req.resource_type,
                    "postData": (req.post_data or "")[:4000],
                })

        page.on("request", on_request)

        def goto(url: str):
            try:
                page.goto(url, wait_until="commit", timeout=20000)
            except PlaywrightTimeoutError as e:
                current = urlparse(page.url)
                if not current.netloc.endswith("hunjang.com"):
                    raise
                errors.append(f"navigation timeout recovered at {page.url}: {e}")
            page.wait_for_timeout(5000)

        try:
            goto(BASE)
            body = page.locator("body").inner_text(timeout=7000)[:30000]
            blocked = bool(BLOCK_RE.search(body))
            if not blocked:
                for selector in (
                    "a:has-text('채용정보')",
                    "button:has-text('채용정보')",
                    "[role='button']:has-text('채용정보')",
                    "nav a:has-text('채용')",
                    "header a:has-text('채용')",
                ):
                    loc = page.locator(selector)
                    if not loc.count():
                        continue
                    try:
                        text = (loc.first.inner_text(timeout=1000) or "").strip()[:120]
                        href = loc.first.get_attribute("href") or ""
                        before = page.url
                        loc.first.click(timeout=3000)
                        page.wait_for_timeout(6000)
                        visits.append({"kind": "nav-click", "selector": selector, "text": text, "href": href, "before": before, "after": page.url})
                        break
                    except Exception as e:
                        errors.append(f"click {selector}: {type(e).__name__}: {e}")

                anchors = page.locator("a[href]").evaluate_all(
                    "els => els.slice(0,1200).map(a => ({href:a.href, text:(a.innerText||'').trim().slice(0,180)}))"
                )
                seen = set()
                for a in anchors:
                    href = str(a.get("href") or "")
                    text = str(a.get("text") or "")
                    if not same_public_origin(href):
                        continue
                    if not (RECRUIT_RE.search(href) or RECRUIT_RE.search(text)):
                        continue
                    if EXCLUDE_RE.search(href) or EXCLUDE_RE.search(text):
                        continue
                    if href in seen:
                        continue
                    seen.add(href)
                    route_candidates.append({"href": href, "text": text})

                for cand in route_candidates[:30]:
                    href = cand["href"]
                    try:
                        before_req = len(requests)
                        goto(href)
                        sample = page.locator("body").inner_text(timeout=5000)[:12000]
                        if BLOCK_RE.search(sample):
                            blocked = True
                            visits.append({"kind": "route", "requested": href, "after": page.url, "blocked": True})
                            break
                        visits.append({
                            "kind": "route",
                            "requested": href,
                            "after": page.url,
                            "title": page.title(),
                            "bodySample": sample[:3000],
                            "newApiRequests": requests[before_req:][:50],
                        })
                    except Exception as e:
                        errors.append(f"visit {href}: {type(e).__name__}: {e}")
        finally:
            ctx.close()
            browser.close()

    observed_api_urls = sorted({r["url"] for r in requests if "/hunApi/" in r.get("url", "")})
    report = {
        "generatedAt": generated_at,
        "source": "훈장마을",
        "publiclyReachable": bool(visits or route_candidates) and not blocked,
        "humanVerificationDetected": blocked,
        "routeCandidateCount": len(route_candidates),
        "routeCandidates": route_candidates[:100],
        "visitCount": len(visits),
        "visits": visits[:100],
        "observedHunApiUrls": observed_api_urls[:300],
        "errors": errors[:100],
        "safety": {
            "tlsVerificationDisabled": False,
            "authenticationBypassed": False,
            "captchaBypassed": False,
            "formsSubmitted": False,
            "publicationAttempted": False,
        },
        "nextGate": "A discovered route/API is only a hint. Qualify a public general list only when the rendered recruitment view emits it; then prove page-wise stable-ID changes and an exhaustion boundary before creating canonical data.",
    }
    Path("hunjang_public_route_probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("generatedAt", "publiclyReachable", "humanVerificationDetected", "routeCandidateCount", "visitCount", "observedHunApiUrls", "nextGate")}, ensure_ascii=False))
    return 0 if report["publiclyReachable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
