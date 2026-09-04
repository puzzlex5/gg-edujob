#!/usr/bin/env python3
from __future__ import annotations
import asyncio,json,re
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import parse_qs
from playwright.async_api import async_playwright
KST=timezone(timedelta(hours=9));URL='https://www.artmore.kr/sub/recruit/search_list.do';OUT=Path('artmore_region_commit_v3.json')
BLOCK_RE=re.compile(r'captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한',re.I);REC_RE=re.compile(r'rec_idx=(\d+)')
async def visible_click(loc):
    for i in range(await loc.count()):
        try:
            if await loc.nth(i).is_visible(): await loc.nth(i).click(); return True
        except Exception: pass
    return False
async def run(b,region,code):
    p=await b.new_page(locale='ko-KR');reqs=[];p.on('request',lambda r:reqs.append({'url':r.url,'method':r.method,'postData':r.post_data}) if 'search_list.do' in r.url else None)
    try:
        resp=await p.goto(URL,wait_until='domcontentloaded',timeout=60000);await p.wait_for_timeout(600)
        if BLOCK_RE.search(await p.locator('body').inner_text()):raise RuntimeError('human-check/block page detected')
        if not await visible_click(p.get_by_role('button',name='지역 선택')):raise RuntimeError('region opener missing')
        await p.wait_for_timeout(300)
        radio=p.locator(f'#area_level_{code}')
        if not await radio.count():raise RuntimeError('region radio missing')
        await radio.evaluate('e=>{e.click();e.dispatchEvent(new Event("change",{bubbles:true}))}')
        await p.wait_for_timeout(600)
        if not await radio.is_checked():raise RuntimeError('region radio did not check')
        all_radio=p.locator('#all_3')
        if not await all_radio.count():raise RuntimeError('region all selector missing after parent selection')
        await all_radio.evaluate('e=>{e.click();e.dispatchEvent(new Event("change",{bubbles:true}))}')
        await p.wait_for_timeout(250)
        selector_vals=await p.locator('input[name="area_selector_val"]').evaluate_all('els=>els.map(e=>e.value)')
        expected=f'2000-{code}'
        if expected not in selector_vals:raise RuntimeError(f'expected area_selector_val {expected} not created: {selector_vals}')
        ok=p.locator('#btn_area_ok')
        if not await visible_click(ok):raise RuntimeError('region selection confirm button missing')
        await p.wait_for_timeout(300)
        array_vals=await p.locator('input[name="array_area_type"]').evaluate_all('els=>els.map(e=>e.value)')
        if expected not in array_vals:raise RuntimeError(f'expected array_area_type {expected} not committed: {array_vals}')
        cur=p.locator('#jobs_ving_chk')
        if not await cur.count():raise RuntimeError('current-only checkbox missing')
        hidden=p.locator('#exclude_end_yn')
        if not await hidden.count():raise RuntimeError('current-only hidden input missing')
        if await hidden.input_value()!='Y':
            # ArtMore binds the Y/N mutation and an immediate form submit to the checkbox click.
            # If the box is visually checked while the hidden value is stale, normalize it to
            # unchecked first, then issue one real click so the site's own handler sets Y.
            if await cur.is_checked():await cur.evaluate('e=>{e.checked=false}')
            try:
                async with p.expect_navigation(wait_until='domcontentloaded',timeout=60000):
                    await cur.click(force=True)
            except Exception:
                # Some runs finish submission before Playwright observes navigation; verify state below.
                await p.wait_for_load_state('domcontentloaded',timeout=60000)
            await p.wait_for_timeout(500)
        if await p.locator('#exclude_end_yn').input_value()!='Y':raise RuntimeError('current-only handler did not set Y')
        array_vals=await p.locator('input[name="array_area_type"]').evaluate_all('els=>els.map(e=>e.value)')
        if expected not in array_vals:raise RuntimeError(f'area filter lost after current-only submit: {array_vals}')
        state=await p.evaluate("""()=>{const f=document.querySelector('form#frm');return{checked:[...document.querySelectorAll('input[name="area"]:checked')].map(e=>({id:e.id,value:e.value})),selectorVals:[...document.querySelectorAll('input[name="area_selector_val"]')].map(e=>e.value),arrayArea:[...document.querySelectorAll('input[name="array_area_type"]')].map(e=>e.value),current:document.querySelector('#exclude_end_yn')?.value||''}}""")
        if not await visible_click(p.get_by_role('button',name='검색하기')):raise RuntimeError('search button missing')
        await p.wait_for_load_state('domcontentloaded',timeout=60000);await p.wait_for_timeout(600)
        posts=[x for x in reqs if x.get('method')=='POST' and x.get('postData')];payload=posts[-1]['postData'] if posts else None;parsed=parse_qs(payload or '',keep_blank_values=True)
        hrefs=await p.locator('a[href*="rec_idx="]').evaluate_all("els=>els.map(a=>a.href||'')");ids=[]
        for h in hrefs:
            m=REC_RE.search(h)
            if m and m.group(1) not in ids:ids.append(m.group(1))
        rows=await p.locator('a[href*="rec_idx="]').evaluate_all("""els=>els.slice(0,30).map(a=>{let n=a,b='';for(let i=0;i<7&&n;i++,n=n.parentElement){const t=(n.innerText||'').replace(/\s+/g,' ').trim();if(t.length>b.length&&t.length<1200)b=t}return b})""")
        other='경기' if region=='서울' else '서울';hits=sum(region in x for x in rows);contam=sum(other in x for x in rows)
        return{'region':region,'code':code,'status':resp.status if resp else None,'preSubmitState':state,'payload':payload,'parsedArea':parsed.get('array_area_type'),'parsedCurrent':parsed.get('exclude_end_yn'),'firstPageIds':ids[:30],'targetHits':hits,'otherMetroContamination':contam,'rowSamples':rows[:10]}
    finally:await p.close()
async def main():
    rep={'generatedAt':datetime.now(KST).isoformat(timespec='seconds'),'source':'아트모아','mode':'read-only-region-commit-probe-v3','publicationEnabled':False,'surfaces':[],'errors':[]}
    async with async_playwright() as pw:
        b=await pw.chromium.launch(headless=True)
        for region,code in [('서울','2001'),('경기','2083')]:
            try:rep['surfaces'].append(await run(b,region,code))
            except Exception as e:rep['errors'].append({'region':region,'error':f'{type(e).__name__}: {e}'})
        await b.close()
    good=[]
    for x in rep['surfaces']:
        expected=f"2000-{x.get('code')}"
        if x.get('parsedArea') and expected in x.get('parsedArea',[]) and x.get('parsedCurrent')==['Y'] and x.get('firstPageIds') and x.get('targetHits',0)>0 and x.get('otherMetroContamination',0)==0:good.append(x)
    rep['summary']={'surfaceCount':len(rep['surfaces']),'errors':len(rep['errors']),'verifiedSurfaceCount':len(good),'readyForCollectorEngineering':len(good)==2 and not rep['errors']}
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(rep['summary'],ensure_ascii=False));return 0 if rep['surfaces'] else 2
if __name__=='__main__':raise SystemExit(asyncio.run(main()))
