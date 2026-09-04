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
ID_JSON_RE = re.compile(r'"(?:id|recruitmentId|recruitment_id)"\s*:\s*"?(\d+)"?', re.I)
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한", re.I)
METRO_RE = re.compile(r"(?:서울(?:특별시)?|경기(?:도)?)\s+[가-힣]+(?:구|시|군)")
STATUS_RE = re.compile(r"D-(?:Day|\d+)|모집\s*중|마감", re.I)
API_HINT_RE = re.compile(r"recruit|posting|job|notice", re.I)


def unique_dicts(items: list[dict], key: str) -> list[dict]:
    out = {}
    for item in items:
        value = str(item.get(key) or "")
        if value:
            out[value] = item
    return list(out.values())


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
        network_evidence: list[dict] = []

        async def capture_response(response):
            try:
                url = response.url
                ctype = (response.headers.get("content-type") or "").lower()
                if not (API_HINT_RE.search(url) or "json" in ctype):
                    return
                entry = {"url": url, "status": response.status, "contentType": ctype}
                if "json" in ctype:
                    try:
                        text = await response.text()
                        entry["bodySample"] = text[:4000]
                        ids = sorted(set(ID_JSON_RE.findall(text)))
                        if ids:
                            entry["candidateIds"] = ids[:100]
                    except Exception:
                        pass
                network_evidence.append(entry)
            except Exception:
                pass

        page.on("response", capture_response)
        try:
            response = await page.goto(LIST_URL, wait_until="networkidle", timeout=60000)
            status = response.status if response else 0
            body = await page.locator("body").inner_text()
            html = await page.content()
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            if BLOCK_RE.search(body):
                raise RuntimeError("human-check/block page detected")

            # 1) Conventional anchors.
            hrefs = await page.locator("a[href]").evaluate_all("els => els.map(a => a.href)")
            detail: list[dict] = []
            for href in hrefs:
                m = DETAIL_RE.search(urlparse(href).path)
                if m:
                    detail.append({"stableId": m.group(1), "url": href, "evidence": "anchor"})

            # 2) SPA/router attributes, inline event handlers and serialized route strings.
            attrs = await page.locator("[href],[data-href],[data-url],[data-link],[data-id],[onclick],[role='link']").evaluate_all(
                "els => els.slice(0,1000).map(e => ({tag:e.tagName,text:(e.innerText||'').slice(0,300),href:e.getAttribute('href'),dataHref:e.getAttribute('data-href'),dataUrl:e.getAttribute('data-url'),dataLink:e.getAttribute('data-link'),dataId:e.getAttribute('data-id'),onclick:e.getAttribute('onclick'),role:e.getAttribute('role')}))"
            )
            route_candidates: list[dict] = []
            for item in attrs:
                raw = " ".join(str(item.get(k) or "") for k in ("href", "dataHref", "dataUrl", "dataLink", "onclick"))
                for m in re.finditer(r"/recruitments/(\d+)", raw):
                    sid = m.group(1)
                    detail.append({"stableId": sid, "url": f"{BASE}/recruitments/{sid}", "evidence": "dom-route"})
                if item.get("dataId") and str(item["dataId"]).isdigit() and API_HINT_RE.search(str(item.get("text") or "")):
                    route_candidates.append(item)
            for m in re.finditer(r"[/\\]recruitments[/\\](\d+)", html):
                sid = m.group(1)
                detail.append({"stableId": sid, "url": f"{BASE}/recruitments/{sid}", "evidence": "serialized-html"})

            # 3) Browser performance/network evidence. Modern SPAs often fetch JSON and render cards without anchors.
            resources = await page.evaluate("performance.getEntriesByType('resource').map(r=>r.name)")
            resource_urls = [u for u in resources if API_HINT_RE.search(u)]
            await page.wait_for_timeout(1500)
            network_evidence[:] = unique_dicts(network_evidence, "url")
            network_ids: set[str] = set()
            for item in network_evidence:
                network_ids.update(str(x) for x in item.get("candidateIds") or [])
            for sid in sorted(network_ids):
                detail.append({"stableId": sid, "url": f"{BASE}/recruitments/{sid}", "evidence": "json-response-id"})

            detail = unique_dicts(detail, "stableId")

            pagination = []
            for href in hrefs + resource_urls + [x.get("url", "") for x in network_evidence]:
                if not href:
                    continue
                if "/recruitments" not in href and not API_HINT_RE.search(href):
                    continue
                if re.search(r"(?:page|cursor|offset|after|limit|size)=", href, re.I):
                    pagination.append(href)
            pagination = list(dict.fromkeys(pagination))

            # 4) Visible card/button samples help infer click routing on the next iteration without guessing.
            visible_candidates = await page.locator("button,[role='button'],[role='link'],li,article").evaluate_all(
                "els => els.filter(e => {const t=(e.innerText||'').trim(); return t.length>=15 && t.length<=1200;}).slice(0,120).map(e => ({tag:e.tagName,role:e.getAttribute('role'),text:(e.innerText||'').replace(/\\s+/g,' ').trim().slice(0,600),outer:e.outerHTML.slice(0,1200)}))"
            )
            samples = [x for x in visible_candidates if METRO_RE.search(str(x.get("text") or "")) or STATUS_RE.search(str(x.get("text") or ""))][:30]

            detail_verified = []
            for item in detail[:3]:
                try:
                    r = await page.goto(item["url"], wait_until="domcontentloaded", timeout=45000)
                    dbody = await page.locator("body").inner_text()
                    m = DETAIL_RE.search(urlparse(page.url).path)
                    detail_verified.append({
                        "stableId": item["stableId"],
                        "httpStatus": r.status if r else 0,
                        "finalUrl": page.url,
                        "stableIdPersists": bool(m and m.group(1) == item["stableId"]),
                        "metroTextPresent": bool(METRO_RE.search(dbody)),
                        "statusTextPresent": bool(STATUS_RE.search(dbody)),
                        "evidence": item.get("evidence"),
                    })
                except Exception as exc:
                    detail_verified.append({"stableId": item["stableId"], "url": item["url"], "error": f"{type(exc).__name__}: {exc}"})

            report["surfaces"].append({
                "surface": "recruitments",
                "requestedUrl": LIST_URL,
                "httpStatus": status,
                "reachable": True,
                "detailLinkCount": len(detail),
                "candidateStableIdCount": len(detail),
                "candidateStableIdExamples": detail[:30],
                "paginationLinkCount": len(pagination),
                "paginationExamples": pagination[:30],
                "metroTextPresent": bool(METRO_RE.search(body)),
                "statusTextPresent": bool(STATUS_RE.search(body)),
                "rowSamples": samples,
                "routeAttributeCandidates": route_candidates[:30],
                "resourceUrlExamples": resource_urls[:50],
                "networkEvidence": network_evidence[:30],
                "detailVerification": detail_verified,
            })
        except Exception as exc:
            report["errors"].append({"surface": "recruitments", "error": f"{type(exc).__name__}: {exc}"})
        finally:
            await browser.close()

    surface = report["surfaces"][0] if report["surfaces"] else {}
    stable = int(surface.get("candidateStableIdCount") or 0) > 0
    verified = surface.get("detailVerification") or []
    detail_ok = bool(verified) and all(x.get("httpStatus") == 200 and x.get("stableIdPersists") for x in verified)
    report["summary"] = {
        "reachable": bool(surface.get("reachable")),
        "stableIdEvidence": stable,
        "detailUrlEvidence": detail_ok,
        "metroTextEvidence": bool(surface.get("metroTextPresent") or any(x.get("metroTextPresent") for x in verified)),
        "statusEvidence": bool(surface.get("statusTextPresent") or any(x.get("statusTextPresent") for x in verified)),
        "paginationEvidence": int(surface.get("paginationLinkCount") or 0) > 0,
        "networkApiEvidence": bool(surface.get("networkEvidence")),
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
