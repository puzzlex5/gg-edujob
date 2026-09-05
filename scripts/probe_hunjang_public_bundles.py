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
API_RE = re.compile(r"[/\"']((?:hunApi|api)/[^\"'\\\s]{3,220})", re.I)
RECRUIT_HINT_RE = re.compile(r"recruit|rcrt|채용", re.I)
NON_GENERAL_RE = re.compile(r"/hunApi/(?:main|common)/|/logApi/|recomm(?:end)?Recruit|recommend", re.I)


def main() -> int:
    generated_at = datetime.now(KST).isoformat(timespec="seconds")
    script_urls: list[str] = []
    script_findings: list[dict] = []
    errors: list[str] = []
    blocked = False

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ko-KR")
        page = ctx.new_page()

        def on_response(resp):
            try:
                req = resp.request
                if req.resource_type != "script":
                    return
                parsed = urlparse(resp.url)
                if not parsed.scheme.startswith("http"):
                    return
                script_urls.append(resp.url)
                text = resp.text()
                if not RECRUIT_HINT_RE.search(text):
                    return
                matches = set()
                for m in API_RE.finditer(text):
                    candidate = "/" + m.group(1).lstrip("/")
                    if RECRUIT_HINT_RE.search(candidate):
                        matches.add(candidate)
                if matches:
                    script_findings.append({
                        "scriptUrl": resp.url,
                        "status": resp.status,
                        "apiCandidates": sorted(matches)[:200],
                    })
            except Exception as e:
                errors.append(f"script response {resp.url}: {type(e).__name__}: {e}")

        page.on("response", on_response)
        try:
            try:
                page.goto(BASE, wait_until="commit", timeout=20000)
            except PlaywrightTimeoutError as e:
                current = urlparse(page.url)
                if not current.netloc.endswith("hunjang.com"):
                    raise
                errors.append(f"navigation timeout recovered at {page.url}: {e}")
            page.wait_for_timeout(7000)
            body = page.locator("body").inner_text(timeout=7000)[:30000]
            blocked = bool(BLOCK_RE.search(body))

            # Trigger only the site's own public recruitment navigation. No authentication,
            # challenge handling, or TLS relaxation is attempted.
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
                        loc.first.click(timeout=3000)
                        page.wait_for_timeout(7000)
                        break
                    except Exception as e:
                        errors.append(f"click {selector}: {type(e).__name__}: {e}")
        finally:
            ctx.close()
            browser.close()

    observed = sorted({u for x in script_findings for u in x.get("apiCandidates", [])})
    general_candidates = sorted({u for u in observed if not NON_GENERAL_RE.search(u)})
    report = {
        "generatedAt": generated_at,
        "source": "훈장마을",
        "publiclyReachable": bool(script_urls) and not blocked,
        "humanVerificationDetected": blocked,
        "scriptCount": len(set(script_urls)),
        "scriptsWithRecruitApiHints": len(script_findings),
        "observedRecruitApiStrings": observed,
        "generalListApiStringCandidates": general_candidates,
        "findings": script_findings[:100],
        "errors": errors[:100],
        "safety": {
            "tlsVerificationDisabled": False,
            "authenticationBypassed": False,
            "captchaBypassed": False,
            "publicationAttempted": False,
        },
        "nextGate": "Treat bundle strings only as discovery hints. A candidate qualifies only after the rendered public recruitment view actually emits the request and pagination proves changing stable IDs plus an exhaustion boundary.",
    }
    Path("hunjang_bundle_probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("generatedAt", "publiclyReachable", "humanVerificationDetected", "scriptCount", "scriptsWithRecruitApiHints", "generalListApiStringCandidates", "nextGate")}, ensure_ascii=False))
    return 0 if report["publiclyReachable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
