#!/usr/bin/env python3
from __future__ import annotations
import asyncio,json,re,os
from datetime import date,datetime
from pathlib import Path
import crawl_artmore_browser as base
OUT=Path('artmore_jobs.candidate.json'); LEDGER=Path('artmore_source_id_ledger.candidate.json'); REPORT=Path('artmore_reconciliation_report.candidate.json'); STATE=Path('artmore_collection_state.candidate.json')
METRO_RE=re.compile(r'(?P<province>서울(?:특별시)?|경기(?:도)?)\s+(?P<district>[^\s]+(?:구|군|시))(?:\s+[^\s]+){0,5}'); PAY_RE=re.compile(r'\s(?:연봉|월급|시급|일급|건당|회사내규|면접후결정)\b')
def expired(v):
    if not v:return False
    try:return date.fromisoformat(v)<base.TODAY
    except Exception:return False
def workplace(text):
    pay=PAY_RE.search(text); limit=pay.start() if pay else len(text); c=[]
    for m in METRO_RE.finditer(text[:limit]): c.append((m.start(),'서울' if m.group('province').startswith('서울') else '경기',m.group(0).strip()))
    if not c:return None,''
    _,p,raw=c[-1]; return p,raw
async def scan_page(page):
    anchors=page.locator('a[href*="rec_idx="]'); raw=[]; metro=[]; excluded=[]; seen=set()
    for i in range(await anchors.count()):
        a=anchors.nth(i); href=await a.get_attribute('href') or ''; m=base.REC_RE.search(href)
        if not m:continue
        rid=m.group(1)
        if rid in seen:continue
        seen.add(rid); raw.append(rid); title=re.sub(r'\s+',' ',(await a.inner_text()).strip())
        text=await a.evaluate("""a=>{let n=a,b=(a.innerText||'').replace(/\s+/g,' ').trim();const rid=(a.getAttribute('href')||'').match(/rec_idx=(\d+)/)?.[1];for(let i=0;i<9&&n;i++,n=n.parentElement){const t=(n.innerText||'').replace(/\s+/g,' ').trim();const ids=[...n.querySelectorAll('a[href*=\"rec_idx=\"]')].map(x=>(x.getAttribute('href')||'').match(/rec_idx=(\d+)/)?.[1]).filter(Boolean);const u=[...new Set(ids)];if(u.length===1&&u[0]===rid&&t.length>b.length&&t.length<1400){b=t;continue}if(u.length>1)break}return b;}""")
        p,loc=workplace(text)
        if not p:continue
        dates=base.DATE_RE.findall(text); reg=base.iso_date(dates[0]) if dates else ''; end=base.iso_date(dates[-1]) if len(dates)>=2 else ''; active='진행중' in text; ended=(bool(base.END_RE.search(text)) and not active) or (expired(end) and not active)
        if ended: excluded.append({'stableId':rid,'province':p,'location':loc,'applyEnd':end}); continue
        metro.append({'sourceIdentity':f'artmore:{rid}','source':'아트모아','sourceSurface':'culture-arts','sourceSurfaceLabel':'아트모아 문화예술 채용','stableId':rid,'province':p,'location':loc,'title':title,'registered':reg,'applyEnd':end,'originalUrl':base.urljoin(base.URL,href),'url':base.urljoin(base.URL,href),'linkedOfficialUrls':[],'rawListText':text})
    return metro,raw,excluded
async def collect(browser):
    page=await browser.new_page(locale='ko-KR')
    try:
        await page.goto(base.URL,wait_until='domcontentloaded',timeout=60000); await page.wait_for_timeout(700)
        if base.BLOCK_RE.search(await page.locator('body').inner_text()):raise RuntimeError('human-check/block page detected')
        out=[]; reports=[]; seen_raw=set(); seen_active=set(); max_pages=int(os.environ.get('ARTMORE_MAX_PAGES','800'))
        for n in range(1,max_pages+1):
            if n>1 and not await base.goto_page(page,n):break
            if base.BLOCK_RE.search(await page.locator('body').inner_text()):raise RuntimeError(f'human-check/block on page {n}')
            rows,raw,excluded=await scan_page(page); new_raw=[x for x in raw if x not in seen_raw]; new_rows=[r for r in rows if r['stableId'] not in seen_active]
            reports.append({'page':n,'rawIdCount':len(raw),'newRawIdCount':len(new_raw),'metroActiveCount':len(rows),'newMetroActiveCount':len(new_rows),'excludedEndedCount':len(excluded),'firstRawId':raw[0] if raw else None,'lastRawId':raw[-1] if raw else None})
            if not raw or not new_raw:break
            seen_raw.update(new_raw)
            for r in new_rows:seen_active.add(r['stableId']);out.append(r)
            if len(raw)<10:break
        if len(reports)>=max_pages and reports[-1].get('newRawIdCount'):raise RuntimeError(f'max page safety cap reached: {max_pages}')
        if not out:raise RuntimeError('zero Seoul/Gyeonggi active rows after unfiltered traversal')
        return out,reports,len(seen_raw)
    finally:await page.close()
async def main():
    gen=datetime.now(base.KST).isoformat(timespec='seconds'); jobs=[];pages=[];raw_count=0;errors=[]
    async with base.async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        try:jobs,pages,raw_count=await collect(browser)
        except Exception as exc:errors.append({'region':'all-unfiltered','error':f'{type(exc).__name__}: {exc}'})
        await browser.close()
    by={j['sourceIdentity']:j for j in jobs};jobs=list(by.values());discovered=set(by);prev=base.previous_ids();healthy=bool(jobs) and not errors
    if prev and len(discovered)<max(10,int(len(prev)*0.35)):healthy=False;errors.append({'region':'all','error':f'suspicious drop: previous={len(prev)} current={len(discovered)}'})
    pc={p:sum(1 for j in jobs if j['province']==p) for p in ('서울','경기')}
    if healthy and any(v==0 for v in pc.values()):healthy=False;errors.append({'region':'all','error':f'zero metro partition: {pc}'})
    ledger={'generatedAt':gen,'source':'아트모아','mode':'candidate-isolated-global-unfiltered','entries':{sid:{'stableId':j['stableId'],'province':j['province'],'url':j['originalUrl'],'presentInLatestScan':True} for sid,j in by.items()}}
    report={'generatedAt':gen,'source':'아트모아','policy':'artmore-global-unfiltered-postfilter-v8-candidate','publicationEnabled':False,'healthy':healthy,'traversalComplete':healthy,'candidateIdCount':len(discovered),'publishedIdCount':len(discovered),'missingAfterCount':0,'missingAfter':[],'rawIdsTraversed':raw_count,'provinceCounts':pc,'sourceReports':[{'surface':'all-unfiltered','pagesVisited':len(pages),'rawIds':raw_count,'ids':len(jobs),'pages':pages}] if pages else [],'errors':errors,'regionsAllowed':['서울','경기'],'nextGate':'detail-link validation + candidate promotion + unified validation'}
    state={'generatedAt':gen,'source':'아트모아','healthyCandidate':healthy,'candidateJobs':len(jobs),'previousLedgerIds':len(prev),'preservedPublishedDataset':True}
    OUT.write_text(json.dumps({'generatedAt':gen,'source':'아트모아','jobs':jobs},ensure_ascii=False,separators=(',',':')),encoding='utf-8');LEDGER.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8');REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'healthy':healthy,'jobs':len(jobs),'provinceCounts':pc,'pagesVisited':len(pages),'rawIdsTraversed':raw_count,'errors':errors},ensure_ascii=False,indent=2));return 0 if healthy else 2
if __name__=='__main__':raise SystemExit(asyncio.run(main()))