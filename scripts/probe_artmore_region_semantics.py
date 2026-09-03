#!/usr/bin/env python3
"""Read-only diagnostic for ArtMore region-filter form semantics.

This probe does not publish jobs and does not bypass TLS/auth/human checks. It
uses the public UI exactly enough to observe how a region selection is converted
into search-form fields and POST payloads, so the collector is not built on a
false assumption that the visible `area` radio is directly serialized.
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
OUT = Path("artmore_region_semantics_report.json")
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한", re.I)
REC_RE = re.compile(r"rec_idx=(\d+)")


async def snapshot(page, tag: str) -> dict:
    return await page.evaluate("""tag => {
      const form = document.querySelector('form#frm');
      const regionEls = [...document.querySelectorAll('input[name="area"]')];
      const interesting = form ? [...form.elements].filter(e =>
        e.name && (/area|region|sido|addr|exclude_end/i.test(e.name) || /area|region|sido|addr|exclude_end/i.test(e.id||''))
      ).map(e => ({name:e.name,id:e.id||'',type:e.type,value:e.value,checked:!!e.checked,disabled:!!e.disabled})) : [];
      const checked = [...document.querySelectorAll('input:checked')].map(e => ({name:e.name,id:e.id||'',value:e.value,type:e.type,formId:e.form?.id||null}));
      return {
        tag,
        formAction: form?.action || null,
        formMethod: form?.method || null,
        regionControls: regionEls.map(e => ({
          id:e.id||'', name:e.name||'', value:e.value||'', checked:!!e.checked,
          disabled:!!e.disabled, formId:e.form?.id||null, formAttr:e.getAttribute('form'),
          parentTag:e.parentElement?.tagName||null,
          outerHTML:e.outerHTML.slice(0,700),
          labelHTML:(document.querySelector(`label[for="${e.id}"]`)?.outerHTML||'').slice(0,700)
        })),
        formInterestingFields: interesting,
        checkedInputs: checked,
        hiddenCandidates: [...document.querySelectorAll('input[type="hidden"]')]
          .filter(e => /area|region|sido|addr|exclude_end/i.test((e.name||'')+' '+(e.id||'')))
          .map(e => ({name:e.name,id:e.id||'',value:e.value,formId:e.form?.id||null})),
      };
    }""", tag)


async def ids(page) -> list[str]:
    hrefs = await page.locator('a[href*="rec_idx="]').evaluate_all("els => els.map(a=>a.href||'')")
    out=[]; seen=set()
    for h in hrefs:
        m=REC_RE.search(h)
        if m and m.group(1) not in seen:
            seen.add(m.group(1)); out.append(m.group(1))
    return out[:20]


async def run_region(browser, region: str, code: str) -> dict:
    page = await browser.new_page(locale="ko-KR")
    requests=[]
    page.on("request", lambda req: requests.append({"url":req.url,"method":req.method,"postData":req.post_data}) if "search_list.do" in req.url else None)
    try:
        resp = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(900)
        body = await page.locator('body').inner_text()
        if BLOCK_RE.search(body):
            raise RuntimeError('human-check/block page detected')

        result={"region":region,"code":code,"initialStatus":resp.status if resp else None,"snapshots":[]}
        result["snapshots"].append(await snapshot(page,"initial"))

        lab = page.locator(f'label[for="area_level_{code}"]')
        radio = page.locator(f'input[name="area"][value="{code}"]')
        if not await radio.count():
            # Preserve evidence instead of assuming the control is permanently absent.
            result["regionControlMissingInitially"] = True
            return result
        if await lab.count():
            await lab.click(force=True)
        else:
            await radio.click(force=True)
        await page.wait_for_timeout(500)
        result["snapshots"].append(await snapshot(page,"after-region-ui-click"))

        current_lab=page.locator('label[for="jobs_ving_chk"]')
        if await current_lab.count():
            await current_lab.click(force=True)
            await page.wait_for_timeout(500)
        result["snapshots"].append(await snapshot(page,"after-current-only-click"))

        # Submit via the site's visible search button so any public JS handlers run.
        btn=page.get_by_role('button', name='검색하기')
        clicked=False
        for i in range(await btn.count()):
            try:
                if await btn.nth(i).is_visible():
                    await btn.nth(i).click(); clicked=True; break
            except Exception:
                pass
        if not clicked:
            raise RuntimeError('visible search button not found')
        await page.wait_for_load_state('domcontentloaded', timeout=60000)
        await page.wait_for_timeout(900)
        result["snapshots"].append(await snapshot(page,"after-submit"))
        result["firstPageIds"] = await ids(page)
        result["bodyRegionSamples"] = [x.strip() for x in (await page.locator('body').inner_text()).splitlines() if region in x][:12]
        posts=[r for r in requests if r.get('method')=='POST' and r.get('postData')]
        result["capturedRequests"] = requests[-12:]
        result["finalPostPayload"] = posts[-1]["postData"] if posts else None
        parsed=parse_qs(result["finalPostPayload"] or '', keep_blank_values=True)
        result["parsedAreaLike"] = {k:v for k,v in parsed.items() if re.search(r'area|region|sido|addr', k, re.I)}
        result["parsedCurrent"] = parsed.get('exclude_end_yn')
        return result
    finally:
        await page.close()


async def main() -> int:
    report={"generatedAt":datetime.now(KST).isoformat(timespec='seconds'),"source":"아트모아","mode":"read-only-region-semantics-probe-v1","publicationEnabled":False,"surfaces":[],"errors":[]}
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        for region,code in (("서울","2001"),("경기","2083")):
            try:
                report["surfaces"].append(await run_region(browser,region,code))
            except Exception as exc:
                report["errors"].append({"region":region,"error":f"{type(exc).__name__}: {exc}"})
        await browser.close()
    area_payloads=[bool(x.get('parsedAreaLike')) for x in report['surfaces']]
    report["summary"]={
        "surfaceCount":len(report['surfaces']),
        "errors":len(report['errors']),
        "areaLikePayloadObserved":bool(area_payloads and all(area_payloads)),
        "readyToPatchCollectorProbe":bool(len(report['surfaces'])==2 and not report['errors'] and all(area_payloads)),
    }
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report['summary'],ensure_ascii=False))
    return 0 if report['surfaces'] else 2

if __name__=='__main__':
    raise SystemExit(asyncio.run(main()))
