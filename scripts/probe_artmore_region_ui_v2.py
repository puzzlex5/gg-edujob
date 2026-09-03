#!/usr/bin/env python3
from __future__ import annotations
import asyncio, json, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs
from playwright.async_api import async_playwright

KST=timezone(timedelta(hours=9))
URL='https://www.artmore.kr/sub/recruit/search_list.do'
OUT=Path('artmore_region_ui_report.json')
BLOCK_RE=re.compile(r'captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한',re.I)
REC_RE=re.compile(r'rec_idx=(\d+)')

async def run(browser, region, code):
    page=await browser.new_page(locale='ko-KR')
    reqs=[]
    page.on('request',lambda r:reqs.append({'url':r.url,'method':r.method,'postData':r.post_data}) if 'search_list.do' in r.url else None)
    try:
        resp=await page.goto(URL,wait_until='domcontentloaded',timeout=60000)
        await page.wait_for_timeout(700)
        if BLOCK_RE.search(await page.locator('body').inner_text()): raise RuntimeError('human-check/block page detected')

        openers=page.get_by_role('button',name='지역 선택')
        opened=False
        for i in range(await openers.count()):
            try:
                if await openers.nth(i).is_visible():
                    await openers.nth(i).click(); opened=True; break
            except Exception: pass
        if not opened:
            # Some builds expose it as a text/link control instead of a button.
            txt=page.get_by_text('지역 선택',exact=True)
            for i in range(await txt.count()):
                try:
                    if await txt.nth(i).is_visible():
                        await txt.nth(i).click(); opened=True; break
                except Exception: pass
        if not opened: raise RuntimeError('visible region selector opener not found')
        await page.wait_for_timeout(400)

        radio=page.locator(f'input[name="area"][value="{code}"]')
        lab=page.locator(f'label[for="area_level_{code}"]')
        if not await radio.count(): raise RuntimeError(f'region radio missing after panel open: {region}={code}')
        clicked=False
        for loc in (lab,radio):
            for i in range(await loc.count()):
                try:
                    if await loc.nth(i).is_visible():
                        await loc.nth(i).click(); clicked=True; break
                except Exception: pass
            if clicked: break
        if not clicked: raise RuntimeError(f'region control not visible after panel open: {region}={code}')
        await page.wait_for_timeout(400)

        # If the region chooser has a visible confirmation button, use it.
        for name in ('선택','확인','적용'):
            cand=page.get_by_role('button',name=name,exact=True)
            done=False
            for i in range(await cand.count()):
                try:
                    if await cand.nth(i).is_visible(): await cand.nth(i).click(); done=True; break
                except Exception: pass
            if done:
                await page.wait_for_timeout(300); break

        cur=page.locator('label[for="jobs_ving_chk"]')
        if not await cur.count(): raise RuntimeError('current-only label missing')
        await cur.click(force=True)
        await page.wait_for_timeout(250)
        current=await page.locator('#exclude_end_yn').input_value()
        if current!='Y': raise RuntimeError(f'current-only hidden field not Y: {current!r}')

        state=await page.evaluate("""() => {
          const f=document.querySelector('form#frm');
          return {
            checked:[...document.querySelectorAll('input:checked')].map(e=>({name:e.name,id:e.id,value:e.value,formId:e.form?.id||null})),
            areaLike:[...document.querySelectorAll('input')].filter(e=>/area|region|sido|addr/i.test((e.name||'')+' '+(e.id||''))).map(e=>({name:e.name,id:e.id,type:e.type,value:e.value,checked:!!e.checked,formId:e.form?.id||null,disabled:!!e.disabled})),
            formAreaLike:f?[...f.elements].filter(e=>/area|region|sido|addr/i.test((e.name||'')+' '+(e.id||''))).map(e=>({name:e.name,id:e.id,type:e.type,value:e.value,checked:!!e.checked,disabled:!!e.disabled})):[]
          };
        }""")

        btn=page.get_by_role('button',name='검색하기')
        submitted=False
        for i in range(await btn.count()):
            try:
                if await btn.nth(i).is_visible(): await btn.nth(i).click(); submitted=True; break
            except Exception: pass
        if not submitted: raise RuntimeError('visible search button not found')
        await page.wait_for_load_state('domcontentloaded',timeout=60000)
        await page.wait_for_timeout(700)
        posts=[x for x in reqs if x.get('method')=='POST' and x.get('postData')]
        payload=posts[-1]['postData'] if posts else None
        parsed=parse_qs(payload or '',keep_blank_values=True)
        hrefs=await page.locator('a[href*="rec_idx="]').evaluate_all("els=>els.map(a=>a.href||'')")
        ids=[]
        for h in hrefs:
            m=REC_RE.search(h)
            if m and m.group(1) not in ids: ids.append(m.group(1))
        rows=await page.locator('a[href*="rec_idx="]').evaluate_all("""els=>els.slice(0,20).map(a=>{let n=a,b='';for(let i=0;i<7&&n;i++,n=n.parentElement){const t=(n.innerText||'').replace(/\s+/g,' ').trim();if(t.length>b.length&&t.length<1200)b=t}return b})""")
        hits=sum(region in x for x in rows)
        other='경기' if region=='서울' else '서울'
        contamination=sum(other in x for x in rows)
        return {'region':region,'code':code,'status':resp.status if resp else None,'preSubmitState':state,'payload':payload,'parsedAreaLike':{k:v for k,v in parsed.items() if re.search(r'area|region|sido|addr',k,re.I)},'parsedCurrent':parsed.get('exclude_end_yn'),'firstPageIds':ids[:20],'targetHits':hits,'otherMetroContamination':contamination,'requests':reqs[-10:]}
    finally: await page.close()

async def main():
    report={'generatedAt':datetime.now(KST).isoformat(timespec='seconds'),'source':'아트모아','mode':'read-only-region-ui-probe-v2','publicationEnabled':False,'surfaces':[],'errors':[]}
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        for region,code in [('서울','2001'),('경기','2083')]:
            try: report['surfaces'].append(await run(b,region,code))
            except Exception as e: report['errors'].append({'region':region,'error':f'{type(e).__name__}: {e}'})
        await b.close()
    good=[x for x in report['surfaces'] if x.get('parsedAreaLike') and x.get('parsedCurrent')==['Y'] and x.get('firstPageIds') and x.get('targetHits',0)>0 and x.get('otherMetroContamination',0)==0]
    report['summary']={'surfaceCount':len(report['surfaces']),'errors':len(report['errors']),'verifiedSurfaceCount':len(good),'readyForCollectorEngineering':len(good)==2 and not report['errors']}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report['summary'],ensure_ascii=False))
    return 0 if report['surfaces'] else 2

if __name__=='__main__': raise SystemExit(asyncio.run(main()))
