#!/usr/bin/env python3
from __future__ import annotations
import asyncio,json,re
from datetime import datetime,timedelta,timezone
from pathlib import Path
from playwright.async_api import async_playwright
KST=timezone(timedelta(hours=9)); URL='https://www.artmore.kr/sub/recruit/search_list.do'; OUT=Path('artmore_region_popup_commit.json')
BLOCK_RE=re.compile(r'captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한',re.I)

async def click_visible(loc):
    for i in range(await loc.count()):
        try:
            if await loc.nth(i).is_visible(): await loc.nth(i).click(); return True
        except Exception: pass
    return False

async def inspect(page,tag):
    return await page.evaluate("""tag=>{
      const root=document.querySelector('#jobs_wkplace_pop');
      const vis=e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0};
      const pack=e=>({tag:e.tagName,id:e.id||'',name:e.getAttribute('name')||'',type:e.getAttribute('type')||'',value:e.getAttribute('value')||'',text:(e.innerText||e.textContent||'').replace(/\s+/g,' ').trim().slice(0,300),cls:e.className||'',onclick:e.getAttribute('onclick')||'',forAttr:e.getAttribute('for')||'',visible:vis(e),outerHTML:e.outerHTML.slice(0,1000)});
      return {tag,popupExists:!!root,popupVisible:root?vis(root):false,popupClass:root?.className||'',controls:root?[...root.querySelectorAll('input,button,a,label')].map(pack).filter(x=>x.visible).slice(0,160):[],checked:root?[...root.querySelectorAll('input:checked')].map(pack):[],popupHTML:(root?.outerHTML||'').slice(0,30000)};
    }""",tag)

async def main():
    report={'generatedAt':datetime.now(KST).isoformat(timespec='seconds'),'source':'아트모아','mode':'read-only-region-popup-commit-probe-v1','publicationEnabled':False,'evidence':{},'errors':[]}
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True); page=await b.new_page(locale='ko-KR')
        try:
            r=await page.goto(URL,wait_until='domcontentloaded',timeout=60000); await page.wait_for_timeout(700)
            if BLOCK_RE.search(await page.locator('body').inner_text()): raise RuntimeError('human-check/block page detected')
            if not await click_visible(page.get_by_role('button',name='지역 선택')): raise RuntimeError('region opener not visible')
            await page.wait_for_timeout(350)
            before=await inspect(page,'opened')
            lab=page.locator('label[for="area_level_2001"]')
            if not await click_visible(lab): raise RuntimeError('Seoul label not visible in popup')
            await page.wait_for_timeout(500)
            after=await inspect(page,'after-seoul-click')
            report['evidence']={'status':r.status if r else None,'before':before,'after':after}
        except Exception as e: report['errors'].append({'error':f'{type(e).__name__}: {e}'})
        await page.close(); await b.close()
    report['summary']={'reachable':bool(report['evidence']),'errors':len(report['errors']),'popupVisibleAfterClick':bool(report.get('evidence',{}).get('after',{}).get('popupVisible')),'visibleControlCountAfter':len(report.get('evidence',{}).get('after',{}).get('controls',[]))}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report['summary'],ensure_ascii=False));return 0 if report['evidence'] else 2
if __name__=='__main__': raise SystemExit(asyncio.run(main()))
