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
        if not await radio.count() or not await radio.is_visible():raise RuntimeError('region radio not visible')
        await radio.evaluate('e=>e.click()');await p.wait_for_timeout(700)
        checked=await radio.is_checked()
        if not checked:raise RuntimeError('DOM click did not check region radio')
        close=p.locator('#jobs_wkplace_pop .popup_close_btn')
        if not await visible_click(close):raise RuntimeError('popup close missing')
        await p.wait_for_timeout(350)
        cur=p.locator('label[for="jobs_ving_chk"]')
        if not await cur.count():raise RuntimeError('current-only label missing')
        await cur.click(force=True);await p.wait_for_timeout(250)
        if await p.locator('#exclude_end_yn').input_value()!='Y':raise RuntimeError('current-only handler did not set Y')
        state=await p.evaluate("""()=>{const f=document.querySelector('form#frm');return{checked:[...document.querySelectorAll('input[name="area"]:checked')].map(e=>({id:e.id,value:e.value,formId:e.form?.id||null})),formArea:f?[...f.elements].filter(e=>/area|region|sido|addr/i.test((e.name||'')+' '+(e.id||''))).map(e=>({name:e.name,id:e.id||'',type:e.type,value:e.value,checked:!!e.checked})):[],current:document.querySelector('#exclude_end_yn')?.value||''}}""")
        if not await visible_click(p.get_by_role('button',name='검색하기')):raise RuntimeError('search button missing')
        await p.wait_for_load_state('domcontentloaded',timeout=60000);await p.wait_for_timeout(600)
        posts=[x for x in reqs if x.get('method')=='POST' and x.get('postData')];payload=posts[-1]['postData'] if posts else None;parsed=parse_qs(payload or '',keep_blank_values=True)
        hrefs=await p.locator('a[href*="rec_idx="]').evaluate_all("els=>els.map(a=>a.href||'')");ids=[]
        for h in hrefs:
            m=REC_RE.search(h)
            if m and m.group(1) not in ids:ids.append(m.group(1))
        rows=await p.locator('a[href*="rec_idx="]').evaluate_all("""els=>els.slice(0,20).map(a=>{let n=a,b='';for(let i=0;i<7&&n;i++,n=n.parentElement){const t=(n.innerText||'').replace(/\s+/g,' ').trim();if(t.length>b.length&&t.length<1200)b=t}return b})""")
        other='경기' if region=='서울' else '서울';hits=sum(region in x for x in rows);contam=sum(other in x for x in rows)
        return{'region':region,'code':code,'status':resp.status if resp else None,'preSubmitState':state,'payload':payload,'parsedAreaLike':{k:v for k,v in parsed.items() if re.search(r'area|region|sido|addr',k,re.I)},'parsedCurrent':parsed.get('exclude_end_yn'),'firstPageIds':ids[:20],'targetHits':hits,'otherMetroContamination':contam,'requests':reqs[-12:]}
    finally:await p.close()
async def main():
    rep={'generatedAt':datetime.now(KST).isoformat(timespec='seconds'),'source':'아트모아','mode':'read-only-region-commit-probe-v3','publicationEnabled':False,'surfaces':[],'errors':[]}
    async with async_playwright() as pw:
        b=await pw.chromium.launch(headless=True)
        for region,code in [('서울','2001'),('경기','2083')]:
            try:rep['surfaces'].append(await run(b,region,code))
            except Exception as e:rep['errors'].append({'region':region,'error':f'{type(e).__name__}: {e}'})
        await b.close()
    good=[x for x in rep['surfaces'] if x.get('parsedAreaLike') and x.get('parsedCurrent')==['Y'] and x.get('firstPageIds') and x.get('targetHits',0)>0 and x.get('otherMetroContamination',0)==0]
    rep['summary']={'surfaceCount':len(rep['surfaces']),'errors':len(rep['errors']),'verifiedSurfaceCount':len(good),'readyForCollectorEngineering':len(good)==2 and not rep['errors']}
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(rep['summary'],ensure_ascii=False));return 0 if rep['surfaces'] else 2
if __name__=='__main__':raise SystemExit(asyncio.run(main()))
