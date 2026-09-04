#!/usr/bin/env python3
from __future__ import annotations

import json, re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

KST=timezone(timedelta(hours=9)); TODAY=datetime.now(KST).date()
BASE='https://www.jobteacher.kr'
SURFACES={
 'seoul': f'{BASE}/employ/lists/all/?is_search=yes&sel_area%5B%5D=1',
 'gyeonggi': f'{BASE}/employ/lists/all/?is_search=yes&sel_area%5B%5D=2422',
}
REGION_TOKEN={'seoul':'서울','gyeonggi':'경기'}
ID_RE=re.compile(r'/employ/detail/(\d+)(?:/|$|\?)',re.I)
BLOCK_RE=re.compile(r'captcha|자동입력|사람인지|접근이\s*제한|비정상적인\s*접근',re.I)
BANNED_RE=re.compile(r'구직|학원\s*매매|악기\s*(?:판매|매매)|연습실|원생\s*모집|학생\s*모집|레슨생\s*모집|팝니다|삽니다|권리금|임대',re.I)
CLOSED_RE=re.compile(r'마감|채용완료|모집완료|종료')
DATE_RE=re.compile(r'^(?:(20\d{2})[./-])?(\d{1,2})[./-](\d{1,2})$')


def with_page(url,n):
 p=urlparse(url); q=parse_qs(p.query,keep_blank_values=True); q['page']=[str(n)]
 return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode([(k,v) for k,vs in q.items() for v in vs]),''))

def max_page(soup):
 vals=[1]
 for a in soup.find_all('a'):
  href=(a.get('href') or '')
  q=parse_qs(urlparse(urljoin(BASE,href)).query)
  for k in ('page','pageno','page_no','nowpage','p'):
   for v in q.get(k,[]):
    if str(v).isdigit(): vals.append(int(v))
 return max(vals)

def parse_modified_date(cells):
 for cell in reversed(cells):
  raw=' '.join(str(cell).split()).strip()
  low=raw.lower()
  if low=='today': return TODAY.isoformat(), raw
  if low=='yesterday': return (TODAY-timedelta(days=1)).isoformat(), raw
  m=DATE_RE.fullmatch(raw)
  if not m: continue
  year=int(m.group(1)) if m.group(1) else TODAY.year
  month,day=int(m.group(2)),int(m.group(3))
  try: d=date(year,month,day)
  except ValueError: continue
  if not m.group(1) and d>TODAY+timedelta(days=31):
   d=date(year-1,month,day)
  return d.isoformat(), raw
 return '', ''

def region_tokens(text):
 out=[]
 if re.search(r'(?:^|\s)서울(?:특별시)?(?:\s|$)', text): out.append('서울')
 if re.search(r'(?:^|\s)(?:경기|경기도)(?:\s|$)', text): out.append('경기')
 return out

def merge_duplicate(existing,new):
 provinces=list(dict.fromkeys((existing.get('provinces') or [existing.get('province')])+(new.get('provinces') or [new.get('province')])))
 provinces=[p for p in provinces if p]
 regions=list(dict.fromkeys((existing.get('regions') or [])+(new.get('regions') or [])))
 locations=list(dict.fromkeys([x for x in (existing.get('location'),new.get('location')) if x]))
 merged=dict(existing)
 merged['provinces']=provinces
 if merged.get('province') not in provinces and provinces: merged['province']=provinces[0]
 merged['regions']=regions
 merged['location']=' / '.join(locations)
 # Keep the richer title/raw row while preserving the earliest stable identity/url.
 if len(str(new.get('title') or ''))>len(str(merged.get('title') or '')): merged['title']=new['title']
 if len(str(new.get('rawRowText') or ''))>len(str(merged.get('rawRowText') or '')): merged['rawRowText']=new['rawRowText']
 # Use the most recent source-reported modification date when both surfaces expose one.
 if str(new.get('registered') or '')>str(merged.get('registered') or ''):
  merged['registered']=new.get('registered',''); merged['registeredText']=new.get('registeredText','')
 return merged

def parse_rows(html,surface):
 soup=BeautifulSoup(html,'html.parser'); token=REGION_TOKEN[surface]; out=[]
 for tr in soup.find_all('tr'):
  a=tr.find('a',href=ID_RE)
  if not a:
   a=next((x for x in tr.find_all('a') if ID_RE.search(x.get('href') or '')),None)
  if not a: continue
  href=urljoin(BASE,a.get('href')); m=ID_RE.search(href)
  if not m: continue
  text=' '.join(tr.stripped_strings); cells=[' '.join(td.stripped_strings) for td in tr.find_all(['td','th'])]
  if token not in text: continue
  if BANNED_RE.search(text) or CLOSED_RE.search(text): continue
  title=' '.join(a.stripped_strings).strip() or text
  locations=[c for c in cells if region_tokens(c)]
  location=' / '.join(dict.fromkeys(locations)) or token
  provinces=region_tokens(text)
  if token not in provinces: provinces.append(token)
  deadline=next((c for c in cells if re.search(r'(?:D-\d+|상시채용|채용시까지|~\s*\d{1,2}\.\d{1,2})',c)), '')
  registered,registered_text=parse_modified_date(cells)
  out.append({'sourceIdentity':f'jobteacher:{m.group(1)}','sourceId':m.group(1),'source':'잡티처','sourceType':'민간 구인','sourceSurface':'academy-recruitment','sourceSurfaceLabel':'잡티처','province':provinces[0] if provinces else token,'provinces':provinces,'regions':locations,'location':location,'title':title,'applyEndText':deadline,'registered':registered,'registeredText':registered_text,'url':href,'rawRowText':text[:900]})
 return out

def load_json(path,default):
 try:return json.loads(Path(path).read_text(encoding='utf-8'))
 except Exception:return default

def main():
 previous=load_json('jobteacher_jobs.json',[]); prev_jobs=previous if isinstance(previous,list) else previous.get('jobs',[])
 old_ledger=load_json('jobteacher_source_id_ledger.json',{'entries':{}}); old_entries=old_ledger.get('entries',{}) if isinstance(old_ledger,dict) else {}
 discovered={}; reports=[]; errors=[]
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True); ctx=browser.new_context(locale='ko-KR'); page=ctx.new_page()
  for surface,start in SURFACES.items():
   try:
    resp=page.goto(start,wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(600); html=page.content()
    if not resp or resp.status!=200 or BLOCK_RE.search(' '.join(BeautifulSoup(html,'html.parser').stripped_strings)): raise RuntimeError('unreachable-or-blocked')
    last=max_page(BeautifulSoup(html,'html.parser')); seen=set(); rows=[]; traversed=0; empty_streak=0
    for n in range(1,last+1):
     if n>1:
      r=page.goto(with_page(start,n),wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(350); html=page.content()
      if not r or r.status!=200: raise RuntimeError(f'page-{n}-http-{r.status if r else 0}')
     batch=parse_rows(html,surface); new=[j for j in batch if j['sourceIdentity'] not in seen]
     for j in new:
      seen.add(j['sourceIdentity']); rows.append(j)
      sid=j['sourceIdentity']; discovered[sid]=merge_duplicate(discovered[sid],j) if sid in discovered else j
     traversed=n; empty_streak=empty_streak+1 if not new else 0
     if n<last and empty_streak>=3: raise RuntimeError(f'premature-empty-streak-at-{n}-of-{last}')
    reports.append({'surface':surface,'pagesExpected':last,'pagesTraversed':traversed,'ids':len(seen),'complete':traversed==last})
   except Exception as e: errors.append({'surface':surface,'error':f'{type(e).__name__}: {e}'})
  ctx.close(); browser.close()
 jobs=sorted(discovered.values(),key=lambda j:int(j['sourceId']),reverse=True)
 now=datetime.now(KST).isoformat(timespec='seconds'); discovered_ids=set(discovered); published_ids={j.get('sourceIdentity') for j in jobs}; missing=sorted(discovered_ids-published_ids)
 traversal_complete=(len(reports)==2 and all(r['complete'] and r['ids']>0 for r in reports) and not errors)
 prev_count=len(prev_jobs); severe_drop=prev_count>=20 and len(jobs)<max(10,int(prev_count*0.45))
 healthy=traversal_complete and not missing and len(jobs)>0 and not severe_drop
 entries=dict(old_entries)
 for sid,j in discovered.items(): entries[sid]={'sourceId':j['sourceId'],'url':j['url'],'lastSeenAt':now,'presentInLatestScan':True,'province':j['province'],'provinces':j.get('provinces',[])}
 for sid,e in entries.items():
  if sid not in discovered_ids and isinstance(e,dict): e['presentInLatestScan']=False
 ledger={'updatedAt':now,'source':'잡티처','entries':entries,'latestDiscoveredIds':sorted(discovered_ids)}
 report={'generatedAt':now,'source':'잡티처','policy':'jobteacher-active-browser-v2','healthy':healthy,'traversalComplete':traversal_complete,'sourceReports':reports,'candidateIdCount':len(discovered_ids),'publishedIdCount':len(published_ids),'missingAfterCount':len(missing),'missingAfter':missing[:100],'errors':errors,'previousPublishedCount':prev_count,'severeDrop':severe_drop,'preservedPreviousDataset':not healthy and bool(prev_jobs),'publicationEnabled':False,'nextGate':'re-crawl with multi-region/date normalization, then unified validator'}
 state={'updatedAt':now,'healthy':healthy,'count':len(jobs) if healthy else prev_count,'lastAttemptCount':len(jobs),'preservedPreviousDataset':not healthy and bool(prev_jobs)}
 if healthy: Path('jobteacher_jobs.json').write_text(json.dumps(jobs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 Path('jobteacher_source_id_ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 Path('jobteacher_reconciliation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 Path('jobteacher_collection_state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(report,ensure_ascii=False)); return 0 if healthy else 2

if __name__=='__main__': raise SystemExit(main())
