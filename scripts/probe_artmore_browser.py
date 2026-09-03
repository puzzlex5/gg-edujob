#!/usr/bin/env python3
"""Read-only browser probe for ArtMore recruitment search.

Discovers durable IDs, dynamic region/deadline controls and pagination mechanics.
It never publishes ArtMore rows into unified search.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.async_api import async_playwright

KST = timezone(timedelta(hours=9))
URL = "https://www.artmore.kr/sub/recruit/search_list.do"
OUT = Path("artmore_probe_report.json")
REC_RE = re.compile(r"rec_idx=(\d+)", re.I)
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한", re.I)


def rec_id(raw: str) -> str | None:
    m = REC_RE.search(raw or "")
    return m.group(1) if m else None


async def controls(page):
    return await page.locator("input,select,button,a,label").evaluate_all("""els => els.map(e => ({
      tag:e.tagName, name:e.name||'', id:e.id||'', type:e.type||'', value:e.value||'',
      checked:!!e.checked, text:(e.innerText||e.textContent||'').trim().replace(/\\s+/g,' ').slice(0,180),
      href:e.getAttribute('href')||'', onclick:e.getAttribute('onclick')||'',
      cls:e.className||''
    }))""")


async def main() -> int:
    report = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "아트모아",
        "baseUrl": "https://www.artmore.kr",
        "mode": "read-only-onboarding-probe",
        "publicationEnabled": False,
        "summary": {}, "evidence": {}, "errors": [],
    }
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="ko-KR")
        try:
            resp = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1800)
            html = await page.content()
            text = await page.locator("body").inner_text()
            base_controls = await controls(page)

            anchors = [x for x in base_controls if x["tag"] == "A"]
            details, seen = [], set()
            for a in anchors:
                sid = rec_id((a.get("href") or "") + " " + (a.get("onclick") or ""))
                if sid and sid not in seen:
                    seen.add(sid)
                    details.append({"rec_idx": sid, "url": f"https://www.artmore.kr/sub/recruit/search_view.do?rec_idx={sid}", "text": a.get("text", "")[:200]})

            pagination = [x for x in anchors if (x.get("text") or "").isdigit() or re.search(r"goPageNavigation|page", (x.get("onclick") or "") + (x.get("href") or ""), re.I)]

            # Dynamic region chooser: ArtMore renders its region checkboxes only after opening the chooser.
            dynamic_region = []
            for selector in ["text=지역 선택", "button:has-text('지역 선택')", "a:has-text('지역 선택')"]:
                try:
                    loc = page.locator(selector)
                    if await loc.count():
                        await loc.first.click(timeout=5000)
                        await page.wait_for_timeout(700)
                        break
                except Exception:
                    continue
            after_region = await controls(page)
            for c in after_region:
                blob = " ".join(str(c.get(k) or "") for k in ("name","id","value","text","onclick","cls"))
                if re.search(r"서울|경기|sido|region|area|loc|addr", blob, re.I):
                    dynamic_region.append(c)

            # Capture exact candidate control values for Seoul/Gyeonggi where available.
            metro_values = []
            for c in dynamic_region:
                if re.search(r"서울|경기", c.get("text") or ""):
                    metro_values.append(c)

            deadline = [c for c in base_controls if re.search(r"exclude_end|deadline|last_date|last_end|마감|종료", " ".join(str(c.get(k) or "") for k in ("name","id","text","value")), re.I)]

            # Verify pagination really changes durable IDs.
            page2_changed = False
            page2_ids = []
            two = page.get_by_text("2", exact=True)
            if await two.count():
                try:
                    await two.first.click(timeout=7000)
                    await page.wait_for_timeout(900)
                    c2 = await controls(page)
                    s2 = []
                    for a in [x for x in c2 if x["tag"] == "A"]:
                        sid = rec_id((a.get("href") or "") + " " + (a.get("onclick") or ""))
                        if sid and sid not in s2:
                            s2.append(sid)
                    page2_ids = s2
                    page2_changed = bool(s2) and set(s2) != set(seen)
                except Exception as exc:
                    report["errors"].append({"stage":"page2","error":f"{type(exc).__name__}: {exc}"})

            detail_ok = False
            detail_status = None
            if details:
                dp = await browser.new_page(locale="ko-KR")
                try:
                    dr = await dp.goto(details[0]["url"], wait_until="domcontentloaded", timeout=60000)
                    detail_status = dr.status if dr else None
                    detail_ok = bool(dr and dr.ok and details[0]["rec_idx"] in dp.url)
                finally:
                    await dp.close()

            blocked = bool(BLOCK_RE.search(text))
            reachable = bool(resp and resp.ok) and len(html) > 5000 and not blocked
            stable = bool(details) and detail_ok
            region_evidence = bool(metro_values) or any(re.search(r"서울|경기", " ".join(str(c.get(k) or "") for k in ("text","value","name","id"))) for c in dynamic_region)
            report["evidence"] = {
                "httpStatus": resp.status if resp else None,
                "finalUrl": page.url,
                "htmlBytes": len(html.encode("utf-8", "ignore")),
                "blockedOrHumanCheck": blocked,
                "visibleTotalText": next((ln.strip() for ln in text.splitlines() if re.search(r"[0-9,]+\s*건", ln)), None),
                "firstPageDurableIdCount": len(details),
                "durableIdExamples": details[:20],
                "detailDirectReachable": detail_ok,
                "detailStatus": detail_status,
                "paginationControls": pagination[:40],
                "page2StableIds": page2_ids[:30],
                "page2ChangesContent": page2_changed,
                "deadlineControls": deadline[:50],
                "dynamicRegionControls": dynamic_region[:120],
                "metroRegionValueCandidates": metro_values[:40],
            }
            report["summary"] = {
                "reachable": reachable,
                "stableIdEvidence": stable,
                "regionFilterEvidence": region_evidence,
                "deadlineFilterEvidence": bool(deadline),
                "paginationControlEvidence": bool(pagination) and page2_changed,
                "readyForCollectorEngineering": reachable and stable and region_evidence and bool(deadline) and bool(pagination) and page2_changed,
                "errors": len(report["errors"]),
            }
        except Exception as exc:
            report["errors"].append({"stage":"main","error":f"{type(exc).__name__}: {exc}"})
            report["summary"] = {"reachable": False, "readyForCollectorEngineering": False, "errors": len(report["errors"])}
        finally:
            await browser.close()

    report["policy"] = {
        "publishBeforeValidation": False,
        "regionsAllowed": ["서울", "경기"],
        "nextGate": "exact metro/current filter params + complete pagination + durable ID ledger + missingAfter=0",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report.get("summary", {}).get("reachable") else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
