#!/usr/bin/env python3
from __future__ import annotations
import asyncio,json,re
from datetime import datetime,timedelta,timezone
from pathlib import Path
from playwright.async_api import async_playwright
KST=timezone(timedelta(hours=9));URL='https://www.artmore.kr/sub/recruit/search_list.do';OUT=Path('artmore_public_js_handlers.json')
BLOCK_RE=re.compile(r'captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한',re.I)
KEYS=('area_level_one','jobs_wkplace_pop','area_level_','exclude_end_yn','goPageNavigation','array_area','wkplace','popClose','search_list.do')
async def main():
    rep={'generatedAt':datetime.now(KST).isoformat(timespec='seconds'),'source':'아트모아','mode':'read-only-public-js-handler-probe-v1','publicationEnabled':False,'scripts':[],'errors':[]}
    async with async_playwright() as pw:
        b=await pw.chromium.launch(headless=True);p=await b.new_page(locale='ko-KR')
        try:
            r=await p.goto(URL,wait_until='domcontentloaded',timeout=60000);await p.wait_for_timeout(700)
            if BLOCK_RE.search(await p.locator('body').inner_text()):raise RuntimeError('human-check/block page detected')
            data=await p.evaluate("""async keys=>{
              const out=[];const scripts=[...document.scripts];
              for(const s of scripts){
                let txt=s.textContent||'';let src=s.src||'';
                if(src && new URL(src,location.href).origin===location.origin){
                  try{txt=await (await fetch(src,{credentials:'same-origin'})).text()}catch(e){txt='FETCH_ERROR:'+e}
                }
                if(!keys.some(k=>txt.includes(k))) continue;
                const snippets=[];
                for(const k of keys){let pos=0,n=0;while((pos=txt.indexOf(k,pos))>=0&&n<8){snippets.push({key:k,snippet:txt.slice(Math.max(0,pos-1400),Math.min(txt.length,pos+2600))});pos+=k.length;n++}}
                out.push({src,inline:!src,length:txt.length,snippets:snippets.slice(0,50)});
              }
              return out;
            }""",list(KEYS))
            rep['httpStatus']=r.status if r else None;rep['scripts']=data
        except Exception as e:rep['errors'].append({'error':f'{type(e).__name__}: {e}'})
        await p.close();await b.close()
    rep['summary']={'httpStatus':rep.get('httpStatus'),'matchingScriptCount':len(rep['scripts']),'snippetCount':sum(len(x.get('snippets',[])) for x in rep['scripts']),'errors':len(rep['errors'])}
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(rep['summary'],ensure_ascii=False));return 0 if rep.get('httpStatus')==200 else 2
if __name__=='__main__':raise SystemExit(asyncio.run(main()))
