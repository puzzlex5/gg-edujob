#!/usr/bin/env python3
"""Read-only browser probe for ArtMore recruitment search.

Discovers durable IDs, form controls, region/deadline filtering controls and
pagination mechanics. It never publishes ArtMore rows into unified search.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

KST = timezone(timedelta(hours=9))
URL = "https://www.artmore.kr/sub/recruit/search_list.do"
OUT = Path("artmore_probe_report.json")
REC_RE = re.compile(r"rec_idx=(\d+)")
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한", re.I)


def rec_id(href: str) -> str | None:
    m = REC_RE.search(href or "")
    return m.group(1) if m else None


async def main() -> int:
    report = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "아트모아",
        "baseUrl": "https://www.artmore.kr",
        "mode": "read-only-onboarding-probe",
        "publicationEnabled": False,
        "summary": {},
        "evidence": {},
        "errors": [],
    }
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="ko-KR")
        try:
            resp = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2500)
            html = await page.content()
            text = await page.locator("body").inner_text()
            anchors = await page.locator("a").evaluate_all(
                "els => els.map(a => ({text:(a.innerText||'').trim(), href:a.href||'', onclick:a.getAttribute('onclick')||''}))"
            )
            forms = await page.locator("form").evaluate_all(
                "forms => forms.map(f => ({action:f.action, method:f.method, id:f.id, name:f.name, controls:[...f.elements].map(e => ({tag:e.tagName,name:e.name,type:e.type,value:e.value,id:e.id,checked:!!e.checked})).filter(x=>x.name||x.id)}))"
            )
            details = []
            for a in anchors:
                rid = rec_id(a.get("href") or "")
                if rid:
                    details.append({"rec_idx": rid, "url": a["href"], "text": a.get("text", "")[:200]})
            # Deduplicate while preserving order.
            uniq = []
            seen = set()
            for d in details:
                if d["rec_idx"] not in seen:
                    seen.add(d["rec_idx"])
                    uniq.append(d)

            page_controls = [a for a in anchors if (a.get("text") or "").strip().isdigit() or re.search(r"page|paging|fn_.*page|go.*page", a.get("onclick") or "", re.I)]
            input_names = sorted({c.get("name") for f in forms for c in f.get("controls", []) if c.get("name")})
            region_controls = [c for f in forms for c in f.get("controls", []) if re.search(r"area|region|sido|addr|loc", (c.get("name") or "") + " " + (c.get("id") or ""), re.I)]
            deadline_controls = [c for f in forms for c in f.get("controls", []) if re.search(r"end|close|deadline|finish|expire", (c.get("name") or "") + " " + (c.get("id") or ""), re.I)]
            blocked = bool(BLOCK_RE.search(text))
            reachable = bool(resp and resp.ok) and len(html) > 5000 and not blocked

            # Test that one durable detail URL is directly reachable.
            detail_ok = False
            detail_status = None
            detail_final = None
            if uniq:
                dpage = await browser.new_page(locale="ko-KR")
                try:
                    dr = await dpage.goto(uniq[0]["url"], wait_until="domcontentloaded", timeout=60000)
                    detail_status = dr.status if dr else None
                    detail_final = dpage.url
                    detail_ok = bool(dr and dr.ok and uniq[0]["rec_idx"] in dpage.url)
                finally:
                    await dpage.close()

            report["evidence"] = {
                "httpStatus": resp.status if resp else None,
                "finalUrl": page.url,
                "htmlBytes": len(html.encode("utf-8", "ignore")),
                "blockedOrHumanCheck": blocked,
                "visibleTotalText": next((ln.strip() for ln in text.splitlines() if re.search(r"[0-9,]+\s*건", ln)), None),
                "firstPageDurableIdCount": len(uniq),
                "durableIdExamples": uniq[:20],
                "detailDirectReachable": detail_ok,
                "detailStatus": detail_status,
                "detailFinalUrl": detail_final,
                "formCount": len(forms),
                "formInputNames": input_names,
                "regionControls": region_controls[:40],
                "deadlineControls": deadline_controls[:40],
                "paginationControls": page_controls[:40],
                "forms": forms[:8],
                "metroTextPresent": bool(re.search(r"서울|경기", text)),
                "activeTextPresent": "진행중" in text,
            }
            stable = len(uniq) > 0 and detail_ok
            controls = bool(region_controls) and bool(page_controls)
            report["summary"] = {
                "reachable": reachable,
                "stableIdEvidence": stable,
                "regionFilterEvidence": bool(region_controls),
                "deadlineFilterEvidence": bool(deadline_controls),
                "paginationControlEvidence": bool(page_controls),
                "readyForCollectorEngineering": reachable and stable and controls,
                "errors": 0,
            }
        except Exception as exc:
            report["errors"].append({"error": f"{type(exc).__name__}: {exc}"})
            report["summary"] = {
                "reachable": False,
                "stableIdEvidence": False,
                "regionFilterEvidence": False,
                "deadlineFilterEvidence": False,
                "paginationControlEvidence": False,
                "readyForCollectorEngineering": False,
                "errors": 1,
            }
        finally:
            await browser.close()
    report["policy"] = {
        "publishBeforeValidation": False,
        "regionsAllowed": ["서울", "경기"],
        "nextGate": "server-side metro/current filter params + complete pagination + missingAfter=0",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"].get("reachable") else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
