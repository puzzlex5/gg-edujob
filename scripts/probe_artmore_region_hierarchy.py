#!/usr/bin/env python3
from __future__ import annotations
import asyncio, json, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from playwright.async_api import async_playwright

KST = timezone(timedelta(hours=9))
URL = 'https://www.artmore.kr/sub/recruit/search_list.do'
OUT = Path('artmore_region_hierarchy_report.json')
BLOCK_RE = re.compile(r'captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한', re.I)

async def visible_click(loc):
    for i in range(await loc.count()):
        try:
            if await loc.nth(i).is_visible():
                await loc.nth(i).click()
                return True
        except Exception:
            pass
    return False

async def inspect_region(browser, region, code):
    page = await browser.new_page(locale='ko-KR')
    try:
        resp = await page.goto(URL, wait_until='domcontentloaded', timeout=60000)
        await page.wait_for_timeout(700)
        body = await page.locator('body').inner_text()
        if BLOCK_RE.search(body):
            raise RuntimeError('human-check/block page detected')
        if not await visible_click(page.get_by_role('button', name='지역 선택')):
            raise RuntimeError('region opener missing')
        await page.wait_for_timeout(350)
        radio = page.locator(f'#area_level_{code}')
        if not await radio.count() or not await radio.is_visible():
            raise RuntimeError('metro first-level radio missing')
        await radio.click(force=True)
        await page.wait_for_timeout(1200)
        state = await page.evaluate("""()=>{
          const simple=e=>({tag:e.tagName,id:e.id||'',name:e.name||'',type:e.type||'',value:e.value||'',checked:!!e.checked,text:(e.innerText||e.textContent||'').replace(/\s+/g,' ').trim().slice(0,160),cls:e.className||'',visible:!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length),outerHTML:e.outerHTML.slice(0,500)});
          const selectors=['.div_area_level_3','.div_area_selector','#jobs_wkplace_pop'];
          const sections={};
          for(const s of selectors){const root=document.querySelector(s);sections[s]=root?{html:root.innerHTML.slice(0,15000),controls:[...root.querySelectorAll('input,button,label,select,option,a')].map(simple).slice(0,250)}:null}
          return {
            checkedFirst:[...document.querySelectorAll('input[name="area"]:checked')].map(simple),
            selectorVals:[...document.querySelectorAll('input[name="area_selector_val"]')].map(simple),
            areaLike:[...document.querySelectorAll('input,select')].filter(e=>/area|region|sido|addr/i.test((e.name||'')+' '+(e.id||'')+' '+(e.className||''))).map(simple).slice(0,300),
            sections
          };
        }""")
        return {'region': region, 'code': code, 'httpStatus': resp.status if resp else None, 'state': state}
    finally:
        await page.close()

async def main():
    report = {'generatedAt': datetime.now(KST).isoformat(timespec='seconds'), 'source': '아트모아', 'mode': 'read-only-region-hierarchy-probe-v1', 'publicationEnabled': False, 'surfaces': [], 'errors': []}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        for region, code in [('서울','2001'), ('경기','2083')]:
            try:
                report['surfaces'].append(await inspect_region(browser, region, code))
            except Exception as exc:
                report['errors'].append({'region': region, 'error': f'{type(exc).__name__}: {exc}'})
        await browser.close()
    report['summary'] = {
        'surfaceCount': len(report['surfaces']),
        'errors': len(report['errors']),
        'selectorValsObserved': sum(len((x.get('state') or {}).get('selectorVals') or []) for x in report['surfaces']),
        'hierarchyObserved': all((x.get('state') or {}).get('sections', {}).get('.div_area_level_3') for x in report['surfaces']) if report['surfaces'] else False,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False))
    return 0 if report['surfaces'] else 2

if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
