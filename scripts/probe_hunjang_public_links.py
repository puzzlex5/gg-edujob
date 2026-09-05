#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

KST = timezone(timedelta(hours=9))
BASE = "https://www.hunjang.com/"
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한|보안문자", re.I)
RECRUIT_RE = re.compile(r"채용|recruit", re.I)
BLOCK_PATH_RE = re.compile(r"login|signin|resume|mypage|member|join|academy/(?:login|recruitMng)|recruitMng", re.I)


def same_public_hunjang(url: str) -> bool:
    p = urlparse(url)
    return p.scheme == "https" and p.netloc.endswith("hunjang.com") and not BLOCK_PATH_RE.search(p.path)


def main() -> int:
    generated_at = datetime.now(KST).isoformat(timespec="seconds")
    discovered_links: dict[str, dict] = {}
    visits = []
    errors = []
    blocked = False

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ko-KR")
        page = ctx.new_page()

        try:
            try:
                page.goto(BASE, wait_until="commit", timeout=20000)
            except PlaywrightTimeoutError:
                if not urlparse(page.url).netloc.endswith("hunjang.com"):
                    raise
            page.wait_for_timeout(4500)
            body = page.locator("body").inner_text(timeout=7000)[:30000]
            blocked = bool(BLOCK_RE.search(body))
            links = page.locator("a[href]").evaluate_all(
                "els => els.slice(0,1200).map(a => ({href:a.href, text:(a.innerText||'').trim().slice(0,160)}))"
            )
            for item in links:
                href = urljoin(BASE, item.get("href") or "")
                text = item.get("text") or ""
                p = urlparse(href)
                if not same_public_hunjang(href):
                    continue
                if not RECRUIT_RE.search(text) and not RECRUIT_RE.search(p.path):
                    continue
                clean = p._replace(fragment="").geturl()
                discovered_links.setdefault(clean, {"url": clean, "text": text})
        except Exception as e:
            errors.append(f"discover: {type(e).__name__}: {e}")

        if not blocked:
            for item in list(discovered_links.values())[:40]:
                api_requests = []

                def on_request(req):
                    if "/hunApi/" in req.url:
                        api_requests.append({
                            "method": req.method,
                            "url": req.url,
                            "postData": (req.post_data or "")[:5000],
                            "resourceType": req.resource_type,
                        })

                page.on("request", on_request)
                try:
                    resp = None
                    try:
                        resp = page.goto(item["url"], wait_until="commit", timeout=20000)
                    except PlaywrightTimeoutError:
                        if not urlparse(page.url).netloc.endswith("hunjang.com"):
                            raise
                    page.wait_for_timeout(5000)
                    text = page.locator("body").inner_text(timeout=7000)[:24000]
                    if BLOCK_RE.search(text):
                        blocked = True
                        visits.append({"requestedUrl": item["url"], "url": page.url, "blocked": True})
                        break
                    ids = sorted(set(re.findall(r"(?:rcrtNo|encRcrtNo)[^0-9]{0,20}([0-9]{3,})", text)))[:100]
                    visits.append({
                        "requestedUrl": item["url"],
                        "linkText": item["text"],
                        "url": page.url,
                        "status": resp.status if resp else None,
                        "title": page.title(),
                        "bodySample": text[:6000],
                        "observedIds": ids,
                        "hunApiRequests": api_requests[:80],
                    })
                except Exception as e:
                    errors.append(f"visit {item['url']}: {type(e).__name__}: {e}")
                finally:
                    page.remove_listener("request", on_request)

        ctx.close()
        browser.close()

    api_urls = sorted({r["url"] for v in visits for r in v.get("hunApiRequests", [])})
    report = {
        "generatedAt": generated_at,
        "source": "훈장마을",
        "publiclyReachable": bool(discovered_links) and not blocked,
        "humanVerificationDetected": blocked,
        "discoveredPublicRecruitLinkCount": len(discovered_links),
        "discoveredPublicRecruitLinks": list(discovered_links.values())[:200],
        "visitedPublicRecruitLinkCount": len(visits),
        "visits": visits,
        "observedHunApiUrls": api_urls,
        "errors": errors,
        "safety": {
            "tlsVerificationDisabled": False,
            "authenticationBypassed": False,
            "captchaBypassed": False,
            "formSubmitted": False,
            "publicationAttempted": False,
        },
        "nextGate": "Use only rendered public-link network evidence to identify a true general recruitment list; then prove page-wise stable-ID changes and exhaustion before any canonical dataset write.",
    }
    Path("hunjang_public_link_probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "publiclyReachable": report["publiclyReachable"],
        "humanVerificationDetected": blocked,
        "discoveredPublicRecruitLinkCount": len(discovered_links),
        "visitedPublicRecruitLinkCount": len(visits),
        "observedHunApiUrlCount": len(api_urls),
        "errors": errors[:5],
    }, ensure_ascii=False))
    return 0 if not blocked else 2


if __name__ == "__main__":
    raise SystemExit(main())
