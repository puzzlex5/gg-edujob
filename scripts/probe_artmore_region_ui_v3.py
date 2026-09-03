#!/usr/bin/env python3
from __future__ import annotations
import asyncio, json, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs
from playwright.async_api import async_playwright

KST=timezone(timedelta(hours=9)); URL='https://www.artmore.kr/sub/recruit/search_list.do'; OUT=Path('artmore_region_ui_v3_report.json')
BLOCK_RE=re.compile(r'captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한',re.I); REC_RE=re.compile(r'rec_idx=(\d+)')

async def click_visible(loc):
    for i in range(await loc.count()):
        try:
            if await loc.nth(i).is_visible(): await loc.nth(i).click(); return True
        except Exception: pass
    return False

async def run(browser,region,code):
    page=await browser.new_page(locale='ko-KR'); reqs=[]
    page.on('request',lambda r:reqs.append({'url':r.url,'method':r.method,'postData':r.post_data}) if 'search_list.do' in r.url else None)
    try:
        resp=await page.goto(URL,wait_until='domcontentloaded',timeout=60000); await page.wait_for_timeout(700)
        if BLOCK_RE.search(await page.locator('body').inner_text()): raise RuntimeError('human-check/block page detected')
        cur=page.locator('label[for="jobs_ving_chk"]')
        if not await cur.count(): raise RuntimeError('current-only label missing')
        await cur.click(force=True); await page.wait_for_timeout(300)
        current=await page.locator('#exclude_end_yn').input_value()
        if current!='Y': raise RuntimeError(f'current-only handler failed before region selection: {current!r}')

        opened=await click_visible(page.get_by_role('button',name='지역 선택'))
        if not opened: opened=await click_visible(page.get_by_text('지역 선택',exact=True))
        if not opened: raise RuntimeError('region selector opener not visible')
        await page.wait_for_timeout(350)
        radio=page.locator(f'input[name="area"][value="{code}"]'); lab=page.locator(f'label[for="area_level_{code}"]')
        if not await radio.count(): raise RuntimeError(f'region radio missing after opener: {region}={code}')
        chosen=await click_visible(lab)
        if not chosen: chosen=await click_visible(radio)
        if not chosen: raise RuntimeError(f'region control not visible: {region}={code}')
        await page.wait_for_timeout(350)
        for nm in ('선택','확인','적용'):
            if await click_visible(page.get_by_role('button',name=nm,exact=True)):
                await page.wait_for_timeout(250); break

        state=await page.evaluate("""() => {const f=document.querySelector('form#frm');return {
          current:document.querySelector('#exclude_end_yn')?.value||null,
          checked:[...document.querySelectorAll('input:checked')].map(e=>({name:e.name,id:e.id,value:e.value,formId:e.form?.id||null})),
          areaLike:[...document.querySelectorAll('input')].filter(e=>/area|region|sido|addr/i.test((e.name||'')+' '+(e.id||''))).map(e=>({name:e.name,id:e.id,type:e.type,value:e.value,checked:!!e.checked,formId:e.form?.id||null,disabled:!!e.disabled})),
          formFields:f?[...f.elements].filter(e=>e.name).map(e=>({name:e.name,id:e.id||'',type:e.type,value:e.value,checked:!!e.checked,disabled:!!e.disabled})):[]};}""")

        if state.get('current')!='Y': raise RuntimeError(f'current-only value lost after region selection: {state.get("current")!r}')
        if not await click_visible(page.get_by_role('button',name='검색하기')): raise RuntimeError('search button not visible')
        await page.wait_for_load_state('domcontentloaded',timeout=60000); await page.wait_for_timeout(700)
        posts=[x for x in reqs if x.get('method')=='POST' and x.get('postData')]; payload=posts[-1]['postData'] if posts else None
        parsed=parse_qs(payload or '',keep_blank_values=True)
        hrefs=await page.locator('a[href*="rec_idx="]').evaluate_all("els=>els.map(a=>a.href||'')")
        ids=[]
        for h in hrefs:
            m=REC_RE.search(h)
            if m and m.group(1) not in ids: ids.append(m.group(1))
        rows=await page.locator('a[href*="rec_idx="]').evaluate_all("""els=>els.slice(0,20).map(a=>{let n=a,b='';for(let i=0;i<7&&n;i++,n=n.parentElement){const t=(n.innerText||'').replace(/\s+/g,' ').trim();if(t.length>b.length&&t.length<1200)b=t}return b})""")
        other='경기' if region=='서울' else '서울'; hits=sum(region in x for x in rows); contamination=sum(other in x for x in rows)
        area_like={k:v for k,v in parsed.items() if re.search(r'area|region|sido|addr',k,re.I)}
        return {'region':region,'code':code,'status':resp.status if resp else None,'preSubmitState':state,'payload':payload,'parsedAreaLike':area_like,'parsedCurrent':parsed.get('exclude_end_yn'),'firstPageIds':ids[:20],'targetHits':hits,'otherMetroContamination':contamination,'requests':reqs[-10:]}
    finally: await page.close()

async def main():
    report={'generatedAt':datetime.now(KST).isoformat(timespec='seconds'),'source':'아트모아','mode':'read-only-region-ui-probe-v3','publicationEnabled':False,'surfaces':[],'errors':[]}
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        for region,code in [('서울','2001'),('경기','2083')]:
            try: report['surfaces'].append(await run(b,region,code))
            except Exception as e: report['errors'].append({'region':region,'error':f'{type(e).__name__}: {e}'})
        await b.close()
    good=[]
    for x in report['surfaces']:
        if x.get('parsedAreaLike') and x.get('parsedCurrent')==['Y'] and x.get('firstPageIds') and x.get('targetHits',0)>0 and x.get('otherMetroContamination',0)==0: good.append(x)
    report['summary']={'surfaceCount':len(report['surfaces']),'errors':len(report['errors']),'verifiedSurfaceCount':len(good),'readyForCollectorEngineering':len(good)==2 and not report['errors']}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(report['summary'],ensure_ascii=False)); return 0 if report['surfaces'] else 2

if __name__=='__main__': raise SystemExit(asyncio.run(main()))
