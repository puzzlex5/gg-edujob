#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright

KST = timezone(timedelta(hours=9))
BASE = "https://00gangsa.com"
LIST_URL = f"{BASE}/recruitments"
DETAIL_RE = re.compile(r"/recruitments/(\d+)(?:$|[/?#])")
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한", re.I)
METRO_RE = re.compile(r"(?:서울(?:특별시)?|경기(?:도)?)\s+[가-힣]+(?:구|시|군)")
STATUS_RE = re.compile(r"D-(?:Day|\d+)|모집\s*중|마감", re.I)


async def main() -> int:
    report = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "공공강사",
        "baseUrl": BASE,
        "mode": "read-only-onboarding-probe",
        "publicationEnabled": False,
        "surfaces": [],
        "summary": {},
        "errors": [],
    }
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(locale="ko-KR")
        try:
            response = await page.goto(LIST_URL, wait_until="networkidle", timeout=60000)
            status = response.status if response else 0
            body = await page.locator("body").inner_text()
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            if BLOCK_RE.search(body):
                raise RuntimeError("human-check/block page detected")

            hrefs = await page.locator("a[href]").evaluate_all("els => els.map(a => a.href)")
            detail = []
            for href in hrefs:
                m = DETAIL_RE.search(urlparse(href).path)
                if m:
                    detail.append({"stableId": m.group(1), "url": href})
            detail = list({x["stableId"]: x for x in detail}.values())

            pagination = []
            for href in hrefs:
                if "/recruitments" not in href or DETAIL_RE.search(urlparse(href).path):
                    continue
                if re.search(r"(?:page|cursor|offset|after)=", href, re.I):
                    pagination.append(href)
            pagination = list(dict.fromkeys(pagination))

            samples = []
            cards = page.locator("a[href*='/recruitments/']")
            for i in range(min(await cards.count(), 30)):
                el = cards.nth(i)
                text = re.sub(r"\s+", " ", (await el.inner_text()).strip())
                href = await el.get_attribute("href") or ""
                m = DETAIL_RE.search(urlparse(urljoin(BASE, href)).path)
                if m and text:
                    samples.append({"stableId": m.group(1), "text": text[:500], "url": urljoin(BASE, href)})

            detail_verified = []
            if detail:
                for item in detail[:3]:
                    r = await page.goto(item["url"], wait_until="domcontentloaded", timeout=45000)
                    dbody = await page.locator("body").inner_text()
                    detail_verified.append({
                        "stableId": item["stableId"],
                        "httpStatus": r.status if r else 0,
                        "finalUrl": page.url,
                        "stableIdPersists": bool(DETAIL_RE.search(urlparse(page.url).path) and DETAIL_RE.search(urlparse(page.url).path).group(1) == item["stableId"]),
                        "metroTextPresent": bool(METRO_RE.search(dbody)),
                        "statusTextPresent": bool(STATUS_RE.search(dbody)),
                    })

            report["surfaces"].append({
                "surface": "recruitments",
                "requestedUrl": LIST_URL,
                "httpStatus": status,
                "reachable": True,
                "detailLinkCount": len(detail),
                "candidateStableIdCount": len(detail),
                "candidateStableIdExamples": detail[:20],
                "paginationLinkCount": len(pagination),
                "paginationExamples": pagination[:20],
                "metroTextPresent": bool(METRO_RE.search(body)),
                "statusTextPresent": bool(STATUS_RE.search(body)),
                "rowSamples": samples[:20],
                "detailVerification": detail_verified,
            })
        except Exception as exc:
            report["errors"].append({"surface": "recruitments", "error": f"{type(exc).__name__}: {exc}"})
        finally:
            await browser.close()

    surface = report["surfaces"][0] if report["surfaces"] else {}
    stable = int(surface.get("candidateStableIdCount") or 0) > 0
    detail_ok = bool(surface.get("detailVerification")) and all(x.get("httpStatus") == 200 and x.get("stableIdPersists") for x in surface.get("detailVerification", []))
    report["summary"] = {
        "reachable": bool(surface.get("reachable")),
        "stableIdEvidence": stable,
        "detailUrlEvidence": detail_ok,
        "metroTextEvidence": bool(surface.get("metroTextPresent") or any(x.get("metroTextPresent") for x in surface.get("detailVerification", []))),
        "statusEvidence": bool(surface.get("statusTextPresent") or any(x.get("statusTextPresent") for x in surface.get("detailVerification", []))),
        "paginationEvidence": int(surface.get("paginationLinkCount") or 0) > 0,
        "readyForCollectorDesign": bool(surface.get("reachable") and stable and detail_ok),
        "errors": len(report["errors"]),
    }
    report["policy"] = {
        "publishBeforeValidation": False,
        "regionsAllowed": ["서울", "경기"],
        "nextGate": "discover complete pagination/filter contract, then isolated full traversal + missingAfter=0",
    }
    Path("public_instructor_probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["surfaces"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
