#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

URL = "https://job.cleaneye.go.kr/user/ypRecruitment.do"
OUT = Path("cleaneye_runtime_probe.json")


async def main() -> int:
    report = {"url": URL, "requests": [], "responses": [], "matches": [], "inputs": [], "functions": {}, "error": None}
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
              searchEntNameBtn: !!document.querySelector('#searchEntNameBtn'),
              searchKeyword: !!document.querySelector('#searchKeyword')
            })""")
            if report["functions"].get("onSearchEntName") != "function":
                raise RuntimeError("window.onSearchEntName is unavailable")
            await page.evaluate("""()=>{
              const kw=document.querySelector('#searchKeyword');
              kw.value='서울문화재단';
              window.onSearchEntName('1');
            }""")
            await page.wait_for_timeout(2000)
            report["inputs"] = await page.locator("input,select,button,a,label").evaluate_all(r"""els=>els.map(e=>({tag:e.tagName,id:e.id||'',name:e.name||'',type:e.type||'',value:e.value||'',text:(e.innerText||e.textContent||'').replace(/\s+/g,' ').trim().slice(0,250),href:e.href||'',onclick:e.getAttribute('onclick')||'',checked:!!e.checked,data:[...e.attributes].filter(a=>a.name.startsWith('data-')).map(a=>[a.name,a.value])})).filter(x=>x.id||x.name||x.text.includes('서울문화재단')||x.value.includes('서울문화재단')||x.href.includes('ypCareers')||x.onclick.includes('yp')||x.onclick.includes('Ent'))""")
            report["matches"] = await page.locator("body *").evaluate_all(r"""els=>els.filter(e=>(e.innerText||'').includes('서울문화재단') && (e.children.length===0 || ['TR','LI','LABEL','BUTTON'].includes(e.tagName))).slice(0,100).map(e=>({tag:e.tagName,id:e.id||'',cls:typeof e.className==='string'?e.className:'',text:(e.innerText||'').replace(/\s+/g,' ').trim().slice(0,500),html:e.outerHTML.slice(0,3500)}))""")
            report["modalHtml"] = await page.locator("#modalSearchForm").evaluate("e=>e.parentElement?.parentElement?.outerHTML.slice(0,30000)||e.outerHTML.slice(0,30000)")
            report["bodyExcerpt"] = (await page.locator("body").inner_text())[-16000:]
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        await ctx.close()
        await browser.close()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"error": report["error"], "functions": report["functions"], "requests": len(report["requests"]), "responses": len(report["responses"]), "matches": len(report["matches"]), "inputs": len(report["inputs"])}, ensure_ascii=False, indent=2))
    return 0 if not report["error"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
