#!/usr/bin/env python3
"""Read-only interaction probe for ArtMore metro/current filter payloads.

Uses ArtMore's public UI in Chromium, captures the exact search request payload,
and records the resulting first-page durable IDs. It does not publish jobs.
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
OUT = Path("artmore_filter_probe_report.json")
REC_RE = re.compile(r"rec_idx=(\d+)")
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한", re.I)


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


async def main() -> int:
    report = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "아트모아",
        "mode": "read-only-filter-payload-probe",
        "publicationEnabled": False,
        "summary": {},
        "evidence": {},
        "errors": [],
    }
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="ko-KR")
        requests_seen: list[dict] = []

        def on_request(req):
            if "search_list.do" in req.url:
                requests_seen.append({
                    "url": req.url,
                    "method": req.method,
                    "postData": req.post_data,
                })

        page.on("request", on_request)
        try:
            resp = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1500)
            body = await page.locator("body").inner_text()
            if BLOCK_RE.search(body):
                raise RuntimeError("human-check/block page detected")

            initial_total = await visible_total(page)
            initial_anchors = await page.locator("a").evaluate_all(
                "els => els.map(a => ({text:(a.innerText||'').trim(),href:a.href||''}))"
            )
            initial_ids = ids_from_anchors(initial_anchors)

            # Record exact HTML around the two controls before interacting.
            snippets = await page.locator("body").evaluate("""body => {
              const all=[...body.querySelectorAll('label,button,a,input,span,div')];
              const pick=t=>all.filter(e=>(e.innerText||e.value||'').includes(t)).slice(0,12).map(e=>({
                tag:e.tagName,id:e.id||'',name:e.getAttribute('name')||'',type:e.getAttribute('type')||'',value:e.value||'',
                text:(e.innerText||'').trim().slice(0,160),html:e.outerHTML.slice(0,900)
              }));
              return {exclude:pick('마감/종료 제외'), region:pick('지역 선택'), seoul:pick('서울'), gyeonggi:pick('경기')};
            }""")

            # Toggle the public current-only control. Prefer its label text, then known hidden field.
            toggled_current = False
            label = page.get_by_text("마감/종료 제외", exact=True)
            if await label.count():
                try:
                    await label.first.click()
                    toggled_current = True
                except Exception:
                    pass
            if not toggled_current:
                cb = page.locator("input[type=checkbox]").filter(has=page.locator("xpath=.."))
                # fallback through labels referencing a checkbox
                for lab in await page.locator("label").all():
                    try:
                        if "마감/종료 제외" in (await lab.inner_text()):
                            await lab.click(); toggled_current = True; break
                    except Exception:
                        continue

            # Open region selector and inspect newly-visible controls.
            region_opened = False
            region_btn = page.get_by_role("button", name=re.compile("지역 선택"))
            if await region_btn.count():
                await region_btn.first.click(); region_opened = True
            else:
                t = page.get_by_text("지역 선택", exact=True)
                if await t.count():
                    await t.first.click(); region_opened = True
            await page.wait_for_timeout(500)

            visible_region_controls = await page.locator("input:visible, button:visible, label:visible, a:visible").evaluate_all(
                "els => els.map(e=>({tag:e.tagName,id:e.id||'',name:e.getAttribute('name')||'',type:e.getAttribute('type')||'',value:e.value||'',checked:!!e.checked,text:(e.innerText||e.value||'').trim().slice(0,180)})).filter(x=>/서울|경기|지역|전체/.test(x.text)).slice(0,160)"
            )

            selected = []
            for name in ("서울", "경기"):
                candidates = page.get_by_text(name, exact=True)
                for i in range(await candidates.count()):
                    el = candidates.nth(i)
                    try:
                        if await el.is_visible():
                            await el.click(); selected.append(name); break
                    except Exception:
                        continue
                await page.wait_for_timeout(150)

            # Close/apply region layer if an apply/confirm button exists.
            for patt in (r"선택완료", r"적용", r"확인"):
                b = page.get_by_role("button", name=re.compile(patt))
                if await b.count():
                    for i in range(await b.count()):
                        try:
                            if await b.nth(i).is_visible():
                                await b.nth(i).click(); await page.wait_for_timeout(250); raise StopAsyncIteration
                        except StopAsyncIteration:
                            break
                        except Exception:
                            continue

            # Capture form state before submit.
            form_state = await page.locator("form#frm").evaluate("""f => [...f.elements].filter(e=>e.name).map(e=>({name:e.name,type:e.type,value:e.value,checked:!!e.checked,id:e.id||''}))""")

            # Submit through the public UI.
            submit_clicked = False
            btn = page.get_by_role("button", name="검색하기")
            if await btn.count():
                for i in range(await btn.count()):
                    try:
                        if await btn.nth(i).is_visible():
                            await btn.nth(i).click(); submit_clicked = True; break
                    except Exception:
                        continue
            if not submit_clicked:
                await page.locator("form#frm").evaluate("f => f.submit()")
            await page.wait_for_load_state("domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1200)

            filtered_total = await visible_total(page)
            filtered_text = await page.locator("body").inner_text()
            filtered_anchors = await page.locator("a").evaluate_all(
                "els => els.map(a => ({text:(a.innerText||'').trim(),href:a.href||''}))"
            )
            filtered_ids = ids_from_anchors(filtered_anchors)
            region_text_ok = bool(re.search(r"서울|경기", filtered_text))
            active_only_first_page = "마감" not in " ".join(a.get("text","") for a in filtered_anchors if REC_RE.search(a.get("href") or ""))

            post_requests = [r for r in requests_seen if r.get("method") == "POST" and r.get("postData")]
            final_payload = post_requests[-1]["postData"] if post_requests else None
            report["evidence"] = {
                "initialStatus": resp.status if resp else None,
                "initialTotal": initial_total,
                "initialFirstPageIds": initial_ids,
                "controlSnippets": snippets,
                "currentToggleInteracted": toggled_current,
                "regionSelectorOpened": region_opened,
                "selectedRegionLabels": selected,
                "visibleRegionControls": visible_region_controls,
                "formStateBeforeSubmit": form_state,
                "capturedSearchRequests": requests_seen[-12:],
                "finalPostPayload": final_payload,
                "filteredFinalUrl": page.url,
                "filteredTotal": filtered_total,
                "filteredFirstPageIds": filtered_ids,
                "filteredFirstPageIdCount": len(filtered_ids),
                "metroTextPresentAfterSubmit": region_text_ok,
                "activeOnlyHeuristic": active_only_first_page,
            }
            has_payload = bool(final_payload)
            metro_selected = set(selected) == {"서울", "경기"}
            changed = filtered_ids and filtered_ids != initial_ids
            report["summary"] = {
                "reachable": True,
                "currentFilterInteraction": toggled_current,
                "regionSelectorInteraction": region_opened,
                "metroSelectionInteraction": metro_selected,
                "serverPayloadCaptured": has_payload,
                "filteredResultChanged": bool(changed),
                "readyForCollectorEngineering": bool(has_payload and metro_selected and changed),
                "errors": 0,
            }
        except Exception as exc:
            report["errors"].append({"error": f"{type(exc).__name__}: {exc}"})
            report["summary"] = {"reachable": False, "readyForCollectorEngineering": False, "errors": 1}
        finally:
            await browser.close()

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"].get("reachable") else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
