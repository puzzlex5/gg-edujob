#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

URL = "https://job.cleaneye.go.kr/user/ypRecruitment.do"
OUT = Path("cleaneye_runtime_probe.json")


async def main() -> int:
    report = {"url": URL, "requests": [], "responses": [], "matches": [], "inputs": [], "functions": {}, "selected": {}, "error": None}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="ko-KR", user_agent="Mozilla/5.0 (Linux; Android 15; Pixel 7) AppleWebKit/537.36 Chrome/151 Mobile Safari/537.36")
        page = await ctx.new_page()
        page.on("request", lambda req: report["requests"].append({"method": req.method, "url": req.url, "postData": req.post_data}) if "cleaneye.go.kr" in req.url else None)
        page.on("response", lambda res: report["responses"].append({"status": res.status, "url": res.url}) if "cleaneye.go.kr" in res.url else None)
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(700)
            report["functions"] = await page.evaluate("""()=>({
              onSearchEntName: typeof window.onSearchEntName,
              likelySearchFunctions: Object.keys(window).filter(k=>/search|recruit/i.test(k) && typeof window[k]==='function').sort().slice(0,100),
              searchEntNameBtn: !!document.querySelector('#searchEntNameBtn'),
              searchKeyword: !!document.querySelector('#searchKeyword')
            })""")
            if report["functions"].get("onSearchEntName") != "function":
                raise RuntimeError("window.onSearchEntName is unavailable")
            await page.evaluate("""()=>{
              const kw=document.querySelector('#searchKeyword'); kw.value='서울문화재단'; window.onSearchEntName('1');
            }""")
            await page.wait_for_timeout(1600)
            report["matches"] = await page.locator("#modal_table tr").evaluate_all(r"""els=>els.map(e=>({text:(e.innerText||'').replace(/\s+/g,' ').trim(),html:e.outerHTML.slice(0,4000)})).filter(x=>x.text.includes('서울문화재단'))""")
            # Trigger the site's own row-selection handler and its modal '선택' button even though
            # this diagnostic does not visually open the modal.
            await page.evaluate("""()=>{
              const a=[...document.querySelectorAll('#modal_table a')].find(x=>(x.innerText||'').trim()==='서울문화재단');
              if(!a) throw new Error('서울문화재단 result anchor missing');
              a.click();
              const b=document.querySelector('#selectEntNameBtn'); if(b) b.click();
            }""")
            await page.wait_for_timeout(600)
            report["selected"] = await page.evaluate(r"""()=>({
              entName: document.querySelector('#entName')?.value||'',
              entNameText: document.querySelector('#entName')?.outerHTML||'',
              searchForm: document.querySelector('#searchForm')?.outerHTML.slice(0,40000)||'',
              fields: [...document.querySelectorAll('#searchForm input,#searchForm select,#searchForm button')].map(e=>({tag:e.tagName,id:e.id||'',name:e.name||'',type:e.type||'',value:e.value||'',text:(e.innerText||e.textContent||'').replace(/\s+/g,' ').trim(),onclick:e.getAttribute('onclick')||''})),
              buttons: [...document.querySelectorAll('button,input[type=button],input[type=submit],a')].map(e=>({tag:e.tagName,id:e.id||'',text:(e.innerText||e.value||'').replace(/\s+/g,' ').trim(),onclick:e.getAttribute('onclick')||''})).filter(x=>/검색|search/i.test(x.text+' '+x.id+' '+x.onclick)).slice(0,80)
            })""")
            # Try the named page search function first; otherwise click the first visible search
            # control associated with searchForm. All network requests are already recorded above.
            ran = await page.evaluate("""()=>{
              for (const name of ['onSearch','searchYpRecruitment','fnSearch','goSearch']) {
                if(typeof window[name]==='function'){ window[name]('1'); return name; }
              }
              const form=document.querySelector('#searchForm');
              const btn=[...(form?.querySelectorAll('button,input[type=button],input[type=submit]')||[])].find(e=>/검색/.test((e.innerText||e.value||'')));
              if(btn){btn.click();return 'button-click';}
              return '';
            }""")
            report["selected"]["searchTrigger"] = ran
            await page.wait_for_timeout(1800)
            report["inputs"] = await page.locator("input,select,button,a,label").evaluate_all(r"""els=>els.map(e=>({tag:e.tagName,id:e.id||'',name:e.name||'',type:e.type||'',value:e.value||'',text:(e.innerText||e.textContent||'').replace(/\s+/g,' ').trim().slice(0,250),href:e.href||'',onclick:e.getAttribute('onclick')||'',checked:!!e.checked})).filter(x=>x.id||x.name||x.text.includes('서울문화재단')||x.value.includes('서울문화재단')||x.href.includes('ypCareers'))""")
            report["resultRows"] = await page.locator("a").evaluate_all(r"""els=>els.map(a=>({text:(a.innerText||'').replace(/\s+/g,' ').trim(),href:a.href||'',onclick:a.getAttribute('onclick')||''})).filter(x=>x.text.includes('서울문화재단')||x.href.includes('ypCareersData'))""")
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        await ctx.close()
        await browser.close()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"error": report["error"], "functions": report["functions"], "selectedEntName": report["selected"].get("entName"), "searchTrigger": report["selected"].get("searchTrigger"), "requests": len(report["requests"]), "matches": len(report["matches"]), "resultRows": len(report.get("resultRows", []))}, ensure_ascii=False, indent=2))
    return 0 if not report["error"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
