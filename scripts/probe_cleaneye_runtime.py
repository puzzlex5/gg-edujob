#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

URL = "https://job.cleaneye.go.kr/user/ypRecruitment.do"
OUT = Path("cleaneye_runtime_probe.json")


async def main() -> int:
    report = {"url": URL, "requests": [], "responses": [], "matches": [], "inputs": [], "error": None}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="ko-KR", user_agent="Mozilla/5.0 (Linux; Android 15; Pixel 7) AppleWebKit/537.36 Chrome/151 Mobile Safari/537.36")
        page = await ctx.new_page()
        page.on("request", lambda req: report["requests"].append({"method": req.method, "url": req.url, "postData": req.post_data}) if ("cleaneye" in req.url and ("Recruit" in req.url or "recruit" in req.url or "ajax" in req.url.lower() or "ent" in req.url.lower())) else None)
        page.on("response", lambda res: report["responses"].append({"status": res.status, "url": res.url}) if ("cleaneye" in res.url and ("Recruit" in res.url or "recruit" in res.url or "ajax" in res.url.lower() or "ent" in res.url.lower())) else None)
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1000)
            kw = page.locator("#searchKeyword")
            if not await kw.count():
                raise RuntimeError("#searchKeyword not found")
            await kw.fill("서울문화재단")
            btn = page.locator("#searchEntNameBtn")
            if not await btn.count():
                raise RuntimeError("#searchEntNameBtn not found")
            await btn.click()
            await page.wait_for_timeout(1800)
            report["inputs"] = await page.locator("input,select,button,a").evaluate_all("""els=>els.map(e=>({tag:e.tagName,id:e.id,name:e.name||'',type:e.type||'',value:e.value||'',text:(e.innerText||e.textContent||'').replace(/\s+/g,' ').trim().slice(0,200),href:e.href||'',onclick:e.getAttribute('onclick')||'',data:[...e.attributes].filter(a=>a.name.startsWith('data-')).map(a=>[a.name,a.value])})).filter(x=>x.id||x.name||x.text.includes('서울문화재단')||x.value.includes('서울문화재단')||x.href.includes('ypCareers')||x.onclick.includes('yp'))""")
            report["matches"] = await page.locator("body *").evaluate_all("""els=>els.filter(e=>(e.innerText||'').includes('서울문화재단') && (e.children.length===0 || e.tagName==='TR' || e.tagName==='LI')).slice(0,80).map(e=>({tag:e.tagName,id:e.id||'',cls:e.className||'',text:(e.innerText||'').replace(/\s+/g,' ').trim().slice(0,500),html:e.outerHTML.slice(0,2500)}))""")
            report["bodyExcerpt"] = (await page.locator("body").inner_text())[-12000:]
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        await ctx.close()
        await browser.close()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"error": report["error"], "requests": len(report["requests"]), "responses": len(report["responses"]), "matches": len(report["matches"]), "inputs": len(report["inputs"])}, ensure_ascii=False, indent=2))
    return 0 if not report["error"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
