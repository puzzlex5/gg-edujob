#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

URL = "https://job.cleaneye.go.kr/user/ypRecruitment.do"
OUT = Path("cleaneye_runtime_probe.json")


async def main() -> int:
    report = {"url": URL, "requests": [], "responses": [], "matches": [], "functions": {}, "selected": {}, "recruitmentResponse": {}, "error": None}
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
              likelySearchFunctions: Object.keys(window).filter(k=>/search|recruit/i.test(k) && typeof window[k]==='function').sort().slice(0,100)
            })""")
            await page.evaluate("""()=>{const kw=document.querySelector('#searchKeyword');kw.value='서울문화재단';window.onSearchEntName('1')}""")
            await page.wait_for_timeout(1400)
            report["matches"] = await page.locator("#modal_table tr").evaluate_all(r"""els=>els.map(e=>({text:(e.innerText||'').replace(/\s+/g,' ').trim(),html:e.outerHTML.slice(0,4000)})).filter(x=>x.text.includes('서울문화재단'))""")
            await page.evaluate("""()=>{const a=[...document.querySelectorAll('#modal_table a')].find(x=>(x.innerText||'').trim()==='서울문화재단');if(!a)throw new Error('result missing');a.click();document.querySelector('#selectEntNameBtn')?.click()}""")
            await page.wait_for_timeout(400)
            report["selected"] = await page.evaluate("""()=>({entName:document.querySelector('#entName')?.value||'',fields:[...document.querySelectorAll('#searchForm input,#searchForm select')].map(e=>({id:e.id||'',name:e.name||'',value:e.value||'',type:e.type||''}))})""")
            async with page.expect_response(lambda r: "/user/selectYpRecruitment.do" in r.url, timeout=15000) as pending:
                trigger = await page.evaluate("""()=>{if(typeof window.onSearch==='function'){window.onSearch('1');return 'onSearch'};const b=[...document.querySelectorAll('#searchForm button')].find(x=>(x.innerText||'').includes('검색'));if(b){b.click();return 'button'};return ''}""")
            res = await pending.value
            report["selected"]["searchTrigger"] = trigger
            report["recruitmentResponse"] = {"status": res.status, "url": res.url, "text": (await res.text())[:200000]}
            await page.wait_for_timeout(500)
            report["resultRows"] = await page.locator("#result_table tr, table.board_job tr").evaluate_all(r"""els=>els.map(e=>({text:(e.innerText||'').replace(/\s+/g,' ').trim(),html:e.outerHTML.slice(0,5000)})).filter(x=>x.text.includes('서울문화재단'))""")
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        await ctx.close(); await browser.close()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"error":report["error"],"entName":report["selected"].get("entName"),"responseChars":len(report["recruitmentResponse"].get("text", "")),"rows":len(report.get("resultRows",[]))}, ensure_ascii=False, indent=2))
    return 0 if not report["error"] else 2


if __name__ == "__main__": raise SystemExit(asyncio.run(main()))
