#!/usr/bin/env python3
from __future__ import annotations
import asyncio, json, re
from datetime import datetime,timedelta,timezone
from pathlib import Path
from playwright.async_api import async_playwright
KST=timezone(timedelta(hours=9)); URL='https://www.artmore.kr/sub/recruit/search_list.do'; OUT=Path('artmore_visible_region_controls.json')
BLOCK_RE=re.compile(r'captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한',re.I)

async def click_visible(loc):
    for i in range(await loc.count()):
        try:
            if await loc.nth(i).is_visible(): await loc.nth(i).click(); return True
        except Exception: pass
    return False

async def main():
    report={'generatedAt':datetime.now(KST).isoformat(timespec='seconds'),'source':'아트모아','mode':'read-only-visible-region-control-probe-v1','publicationEnabled':False,'evidence':{},'errors':[]}
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True); page=await b.new_page(locale='ko-KR')
        try:
            r=await page.goto(URL,wait_until='domcontentloaded',timeout=60000); await page.wait_for_timeout(700)
            body=await page.locator('body').inner_text()
            if BLOCK_RE.search(body): raise RuntimeError('human-check/block page detected')
            opened=await click_visible(page.get_by_role('button',name='지역 선택'))
            if not opened: opened=await click_visible(page.get_by_text('지역 선택',exact=True))
            if not opened: raise RuntimeError('region selector opener not visible')
            await page.wait_for_timeout(500)
            data=await page.evaluate("""() => {
              const vis=e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0};
              const pack=e=>({tag:e.tagName,id:e.id||'',name:e.getAttribute('name')||'',type:e.getAttribute('type')||'',value:e.getAttribute('value')||'',text:(e.innerText||e.textContent||'').replace(/\s+/g,' ').trim().slice(0,300),cls:e.className||'',onclick:e.getAttribute('onclick')||'',forAttr:e.getAttribute('for')||'',href:e.getAttribute('href')||'',visible:vis(e),outerHTML:e.outerHTML.slice(0,900)});
              const matches=[...document.querySelectorAll('*')].filter(e=>{const t=(e.innerText||e.textContent||'').trim();return t==='서울'||t==='경기'||/서울/.test(e.getAttribute('aria-label')||'')||/경기/.test(e.getAttribute('aria-label')||'')});
              const visibleInputs=[...document.querySelectorAll('input,button,a,label,select')].filter(vis).map(pack).filter(x=>/서울|경기|지역|선택|area|region/i.test(x.text+' '+x.id+' '+x.name+' '+x.value+' '+x.onclick+' '+x.forAttr)).slice(0,100);
              const areaAll=[...document.querySelectorAll('input[name="area"], [id*="area"], [name*="area"]')].map(pack).slice(0,100);
              return {matchedTextElements:matches.map(pack).slice(0,100),visibleCandidates:visibleInputs,areaAll};
            }""")
            report['evidence']={'status':r.status if r else None,**data}
        except Exception as e: report['errors'].append({'error':f'{type(e).__name__}: {e}'})
        await page.close(); await b.close()
    report['summary']={'reachable':bool(report['evidence']),'errors':len(report['errors']),'visibleCandidateCount':len(report.get('evidence',{}).get('visibleCandidates',[])),'matchedTextCount':len(report.get('evidence',{}).get('matchedTextElements',[]))}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(report['summary'],ensure_ascii=False)); return 0 if report['evidence'] else 2
if __name__=='__main__': raise SystemExit(asyncio.run(main()))
