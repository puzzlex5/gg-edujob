#!/usr/bin/env python3
"""Read-only interaction probe for ArtMore current/metro filter payloads.

The public ArtMore form already contains hidden region radio controls.  This probe
extracts their exact labels/codes, then submits Seoul and Gyeonggi independently
through the public form while also enabling the public current-only checkbox.
It records the exact POST payload and first-page durable IDs/locations.  It never
publishes jobs and never disables TLS verification.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

from playwright.async_api import async_playwright

KST = timezone(timedelta(hours=9))
URL = "https://www.artmore.kr/sub/recruit/search_list.do"
OUT = Path("artmore_filter_probe_report.json")
REC_RE = re.compile(r"rec_idx=(\d+)")
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한", re.I)
REGION_RE = {
    "서울": re.compile(r"(?:^|\s)서울(?:\s|$)|서울\s*[^\s]*구"),
    "경기": re.compile(r"(?:^|\s)경기(?:\s|$)|경기\s*[^\s]*시|경기\s*[^\s]*군"),
}


def ids_from_anchors(anchors: list[dict]) -> list[str]:
    out, seen = [], set()
    for a in anchors:
        m = REC_RE.search(a.get("href") or "")
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(m.group(1))
    return out


async def visible_total(page) -> str | None:
    text = await page.locator("body").inner_text()
    for line in text.splitlines():
        if re.search(r"[0-9,]+\s*건", line):
            return line.strip()
    return None


async def anchors(page) -> list[dict]:
    return await page.locator("a").evaluate_all(
        "els => els.map(a => ({text:(a.innerText||'').trim(),href:a.href||''}))"
    )


async def extract_region_map(page) -> list[dict]:
    return await page.locator('input[name="area"]').evaluate_all("""els => els.map(e => {
      const lab = document.querySelector(`label[for="${e.id}"]`);
      return {id:e.id||'', name:e.name||'', value:e.value||'', label:(lab?.innerText||'').trim()};
    })""")


async def submit_surface(browser, region: str, area_code: str) -> dict:
    page = await browser.new_page(locale="ko-KR")
    requests_seen: list[dict] = []

    def on_request(req):
        if "search_list.do" in req.url:
            requests_seen.append({"url": req.url, "method": req.method, "postData": req.post_data})

    page.on("request", on_request)
    try:
        resp = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1000)
        body = await page.locator("body").inner_text()
        if BLOCK_RE.search(body):
            raise RuntimeError("human-check/block page detected")

        # Use the site's own public current-only control.
        current = page.locator("#jobs_ving_chk")
        if await current.count():
            await current.check(force=True)
        else:
            raise RuntimeError("current-only checkbox not found")

        # Select the exact top-level public region radio already present in DOM.
        radio = page.locator(f'input[name="area"][value="{area_code}"]')
        if not await radio.count():
            raise RuntimeError(f"area radio not found for {region}={area_code}")
        await radio.check(force=True)
        await page.wait_for_timeout(350)

        form_state = await page.locator("form#frm").evaluate("""f => [...f.elements]
          .filter(e=>e.name).map(e=>({name:e.name,type:e.type,value:e.value,checked:!!e.checked,id:e.id||''}))""")

        # Submit through public form semantics so successful serialization itself is evidence.
        btn = page.get_by_role("button", name="검색하기")
        clicked = False
        for i in range(await btn.count()):
            try:
                if await btn.nth(i).is_visible():
                    await btn.nth(i).click(); clicked = True; break
            except Exception:
                continue
        if not clicked:
            await page.locator("form#frm").evaluate("f => f.submit()")

        await page.wait_for_load_state("domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1000)
        text = await page.locator("body").inner_text()
        aa = await anchors(page)
        ids = ids_from_anchors(aa)
        post = [r for r in requests_seen if r.get("method") == "POST" and r.get("postData")]
        payload = post[-1]["postData"] if post else None
        parsed = parse_qs(payload or "", keep_blank_values=True)

        # Each result link's nearby row/card text is stronger evidence than the page-wide text.
        rows = await page.locator('a[href*="rec_idx="]').evaluate_all("""els => {
          const out=[]; const seen=new Set();
          for (const a of els) {
            const m=(a.href||'').match(/rec_idx=(\d+)/); if(!m||seen.has(m[1])) continue;
            seen.add(m[1]);
            let n=a; for(let i=0;i<6 && n;i++,n=n.parentElement){
              const t=(n.innerText||'').replace(/\s+/g,' ').trim();
              if(t.length>=20){out.push({id:m[1],text:t.slice(0,700)}); break;}
            }
          }
          return out.slice(0,20);
        }""")
        target_re = REGION_RE[region]
        region_hits = sum(1 for x in rows if target_re.search(x.get("text", "")))
        other_region = "경기" if region == "서울" else "서울"
        contamination = sum(1 for x in rows if REGION_RE[other_region].search(x.get("text", "")))

        return {
            "region": region,
            "areaCode": area_code,
            "httpStatus": resp.status if resp else None,
            "total": await visible_total(page),
            "firstPageIds": ids,
            "firstPageIdCount": len(ids),
            "resultRows": rows,
            "targetRegionHitCount": region_hits,
            "otherMetroContaminationCount": contamination,
            "formStateBeforeSubmit": form_state,
            "capturedSearchRequests": requests_seen[-8:],
            "finalPostPayload": payload,
            "serializedArea": (parsed.get("area") or [None])[-1],
            "serializedCurrentOnly": (parsed.get("exclude_end_yn") or [None])[-1],
            "payloadVerified": bool(payload and (parsed.get("area") or [None])[-1] == area_code and (parsed.get("exclude_end_yn") or [None])[-1] == "Y"),
            "regionResultVerified": bool(ids and rows and region_hits > 0 and contamination == 0),
        }
    finally:
        await page.close()


async def main() -> int:
    report = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "아트모아",
        "mode": "read-only-filter-payload-probe-v2",
        "publicationEnabled": False,
        "summary": {},
        "evidence": {},
        "errors": [],
    }
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        discovery = await browser.new_page(locale="ko-KR")
        try:
            resp = await discovery.goto(URL, wait_until="domcontentloaded", timeout=60000)
            await discovery.wait_for_timeout(1000)
            body = await discovery.locator("body").inner_text()
            if BLOCK_RE.search(body):
                raise RuntimeError("human-check/block page detected")
            initial_total = await visible_total(discovery)
            initial_ids = ids_from_anchors(await anchors(discovery))
            region_map = await extract_region_map(discovery)
            by_label = {x.get("label"): x.get("value") for x in region_map if x.get("label") and x.get("value")}
            seoul = by_label.get("서울")
            gyeonggi = by_label.get("경기")
            if not seoul or not gyeonggi:
                raise RuntimeError(f"could not discover Seoul/Gyeonggi area codes: {by_label}")
            await discovery.close()

            surfaces = []
            for region, code in (("서울", seoul), ("경기", gyeonggi)):
                try:
                    surfaces.append(await submit_surface(browser, region, code))
                except Exception as exc:
                    report["errors"].append({"region": region, "error": f"{type(exc).__name__}: {exc}"})

            ready = (
                len(surfaces) == 2
                and not report["errors"]
                and all(s.get("payloadVerified") for s in surfaces)
                and all(s.get("regionResultVerified") for s in surfaces)
                and all(int(s.get("firstPageIdCount") or 0) > 0 for s in surfaces)
            )
            report["evidence"] = {
                "initialStatus": resp.status if resp else None,
                "initialTotal": initial_total,
                "initialFirstPageIds": initial_ids,
                "regionMap": region_map,
                "metroAreaCodes": {"서울": seoul, "경기": gyeonggi},
                "surfaces": surfaces,
            }
            report["summary"] = {
                "reachable": True,
                "regionCodesDiscovered": True,
                "currentFilterInteraction": True,
                "serverPayloadCaptured": all(s.get("finalPostPayload") for s in surfaces) if surfaces else False,
                "metroServerFilteringVerified": all(s.get("regionResultVerified") for s in surfaces) if surfaces else False,
                "readyForCollectorEngineering": ready,
                "errors": len(report["errors"]),
            }
        except Exception as exc:
            report["errors"].append({"error": f"{type(exc).__name__}: {exc}"})
            report["summary"] = {"reachable": False, "readyForCollectorEngineering": False, "errors": len(report["errors"])}
        finally:
            try:
                if not discovery.is_closed():
                    await discovery.close()
            except Exception:
                pass
            await browser.close()

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"].get("reachable") else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
