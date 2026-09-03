#!/usr/bin/env python3
"""Lessoninfo exact recruitment collector.

Targets exactly the two public recruitment surfaces supplied by the site owner/user:
- 방과후/강사 구인: /board/board.php?board_code=20130529164321_7227&code=&bo_table=20170316171033_6494
- 문화예술구인: /culture-jobs/list.php

Safety: never accept a CAPTCHA/challenge or empty first page as a healthy empty board,
never overwrite the last known-good dataset on incomplete traversal, and dedupe only
by stable source identity / canonical detail URL.
"""
from __future__ import annotations
import hashlib, json, re
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse
from bs4 import BeautifulSoup
import crawl_lessoninfo_jobs as c

BASES=["https://www.lessoninfo.co.kr","https://lessoninfo.co.kr"]
BOARD={"board_code":"20130529164321_7227","code":"","bo_table":"20170316171033_6494"}
CHALLENGE=re.compile(r"자동등록방지|보안절차|prove that you are human|captcha|access denied",re.I)

def is_challenge(html):
    return bool(CHALLENGE.search(BeautifulSoup(html or "","html.parser").get_text(" ",strip=True)))

def canon(u):
    p=urlparse(u); return urlunparse((p.scheme,p.netloc,p.path,"",p.query,""))

def sid_for(url):
    q=parse_qs(urlparse(url).query); bt=(q.get("bo_table")or[""])[0]; wr=(q.get("wr_no")or[""])[0]
    if bt and wr.isdigit(): return f"{bt}:{wr}"
    return "culture:"+hashlib.sha1(canon(url).encode()).hexdigest()[:24]

def parse_culture_list(html,base):
    if is_challenge(html): raise RuntimeError("human_verification_challenge")
    soup=BeautifulSoup(html,"html.parser"); out={}
    for a in soup.find_all("a",href=True):
        u=canon(urljoin(base,a["href"])); p=urlparse(u)
        if p.netloc not in {"lessoninfo.co.kr","www.lessoninfo.co.kr"} or not p.path.startswith("/culture-jobs/"): continue
        if p.path.rstrip("/") in {"/culture-jobs","/culture-jobs/list.php"}: continue
        q=parse_qs(p.query)
        looks_detail=any(k.lower() in {"idx","id","no","seq","wr_no","job_no","job_seq"} and v for k,v in q.items()) or p.path.lower() not in {"/culture-jobs/write.php"}
        if not looks_detail: continue
        title=" ".join(a.stripped_strings).strip() or (a.get("title")or"").strip()
        parent=a.find_parent(["tr","li","article","div"]); row=" ".join(parent.stripped_strings) if parent else title
        signal=f"{title} {row}"
        if c.EXCLUDE_RE.search(signal) and not c.RECRUIT_RE.search(signal): continue
        sid=sid_for(u); out.setdefault(sid,{"sourceIdentity":sid,"url":u,"titleHint":title,"rowText":row,"registeredHint":c.extract_date(row),"surface":"culture-jobs"})
    return list(out.values())

def crawl_board(s,base):
    found={}; fps=set(); pages=0; old=0
    for page in range(1,c.MAX_PAGES+1):
        params={**BOARD,"page":page}; url=base+"/board/board.php?"+urlencode(params); r=c.get(s,url); pages+=1
        if is_challenge(r.text): return list(found.values()),{"surface":"방과후/강사 구인","complete":False,"pagesScanned":pages,"ids":len(found),"stopReason":"human_verification_challenge"}
        rows=c.parse_list(r.text,base)
        if not rows:
            ok=page>1 and bool(found); return list(found.values()),{"surface":"방과후/강사 구인","complete":ok,"pagesScanned":pages,"ids":len(found),"stopReason":"end_of_pages" if ok else "unexpected_empty_first_page"}
        ids=tuple(sorted(x["sourceIdentity"] for x in rows))
        if ids in fps: return list(found.values()),{"surface":"방과후/강사 구인","complete":True,"pagesScanned":pages,"ids":len(found),"stopReason":"repeated_page_boundary"}
        fps.add(ids)
        for x in rows: x["surface"]="lesson-board"; found.setdefault(x["sourceIdentity"],x)
        dated=[x.get("registeredHint") for x in rows if x.get("registeredHint")]
        if dated and all(not c.is_recent(d) for d in dated):
            old+=1
            if old>=2: return list(found.values()),{"surface":"방과후/강사 구인","complete":True,"pagesScanned":pages,"ids":len(found),"stopReason":"lookback_crossed"}
        else: old=0
    return list(found.values()),{"surface":"방과후/강사 구인","complete":False,"pagesScanned":pages,"ids":len(found),"stopReason":"max_pages"}

def crawl_culture(s,base):
    found={}; fps=set(); pages=0; old=0
    for page in range(1,c.MAX_PAGES+1):
        url=base+"/culture-jobs/list.php"+("" if page==1 else f"?page={page}"); r=c.get(s,url); pages+=1
        if is_challenge(r.text): return list(found.values()),{"surface":"문화예술구인","complete":False,"pagesScanned":pages,"ids":len(found),"stopReason":"human_verification_challenge"}
        rows=parse_culture_list(r.text,base)
        if not rows:
            ok=page>1 and bool(found); return list(found.values()),{"surface":"문화예술구인","complete":ok,"pagesScanned":pages,"ids":len(found),"stopReason":"end_of_pages" if ok else "unexpected_empty_first_page"}
        ids=tuple(sorted(x["sourceIdentity"] for x in rows))
        if ids in fps: return list(found.values()),{"surface":"문화예술구인","complete":True,"pagesScanned":pages,"ids":len(found),"stopReason":"repeated_page_boundary"}
        fps.add(ids)
        for x in rows: found.setdefault(x["sourceIdentity"],x)
        dated=[x.get("registeredHint") for x in rows if x.get("registeredHint")]
        if dated and all(not c.is_recent(d) for d in dated):
            old+=1
            if old>=2: return list(found.values()),{"surface":"문화예술구인","complete":True,"pagesScanned":pages,"ids":len(found),"stopReason":"lookback_crossed"}
        else: old=0
    return list(found.values()),{"surface":"문화예술구인","complete":False,"pagesScanned":pages,"ids":len(found),"stopReason":"max_pages"}

def detail(s,item):
    if item.get("surface")=="lesson-board": return c.parse_detail(s,item)
    r=c.get(s,item["url"])
    if is_challenge(r.text): raise RuntimeError("human_verification_challenge")
    soup=BeautifulSoup(r.text,"html.parser"); title=item.get("titleHint","")
    for sel in ("h1","h2","h3",".subject",".title","title"):
        el=soup.select_one(sel)
        if el:
            t=" ".join(el.stripped_strings).strip()
            if len(t)>=2: title=t; break
    text=" ".join(soup.stripped_strings); signal=title+" "+text[:12000]
    if c.EXCLUDE_RE.search(title) and not c.RECRUIT_RE.search(title): return None
    if not c.RECRUIT_RE.search(signal): return None
    reg=item.get("registeredHint") or c.extract_date(text)
    if reg and not c.is_recent(reg): return None
    metro=bool(c.SEOUL_GYEONGGI_RE.search(signal))
    return {"sourceIdentity":item["sourceIdentity"],"source":"레슨인포","sourceType":"민간 구인","trustLevel":"민간출처","category":"private-recruitment","sourceSurface":"culture-jobs","title":title[:300],"registered":reg,"url":item["url"],"originalUrl":item["url"],"metroConfirmed":metro,"needsLocationReview":not metro,"collectedAt":c.now_kst().isoformat(timespec="seconds")}

def main():
    s=c.session(); base=None; robots=""
    for b in BASES:
        allowed,status=c.check_robots(s,b); robots=status
        if not allowed: continue
        try:
            r=c.get(s,b+"/")
            if r.ok: base=b; break
        except Exception: pass
    if not base:
        c.write_json(c.REPORT,{"generatedAt":c.now_kst().isoformat(timespec="seconds"),"healthy":False,"blocker":"source unavailable or robots disallowed","robotsStatus":robots,"preservedPreviousDataset":c.OUT.exists()}); return 2
    candidates={}; reports=[]; complete=True; errors=[]
    for fn in (crawl_board,crawl_culture):
        try:
            rows,rp=fn(s,base); reports.append(rp); complete &= bool(rp.get("complete"))
            for x in rows: candidates.setdefault(x["sourceIdentity"],x)
        except Exception as e:
            complete=False; reports.append({"surface":fn.__name__,"complete":False,"error":f"{type(e).__name__}:{e}"}); errors.append(fn.__name__)
    previous=c.read_json(c.OUT,{"jobs":[]}); prev=previous if isinstance(previous,list) else previous.get("jobs",[]); by={j.get("sourceIdentity"):j for j in prev if j.get("sourceIdentity")}
    eligible=set(); rejected=set(); derr=[]; parsed=0
    for sid,item in candidates.items():
        try:
            j=detail(s,item)
            if j: by[sid]=j; eligible.add(sid); parsed+=1
            else: rejected.add(sid); by.pop(sid,None)
        except Exception as e:
            derr.append({"id":sid,"error":f"{type(e).__name__}:{e}"}); complete=False
    missing=sorted(eligible-set(by)); empty=not candidates; healthy=complete and not empty and not missing and not derr
    jobs=sorted(by.values(),key=lambda j:(j.get("registered")or"",j.get("sourceIdentity")or""),reverse=True) if healthy else prev
    if healthy: c.write_json(c.OUT,{"updatedAt":c.now_kst().strftime("%Y-%m-%d %H:%M KST"),"source":"레슨인포","category":"private-recruitment","jobs":jobs})
    now=c.now_kst().isoformat(timespec="seconds"); ledger=c.read_json(c.LEDGER,{"ids":{},"snapshots":[]}); ledger.setdefault("ids",{})
    for sid,item in candidates.items():
        e=ledger["ids"].setdefault(sid,{"firstSeenAt":now}); e.update({"lastSeenAt":now,"url":item["url"],"surface":item.get("surface"),"classification":"recruitment" if sid in eligible else "excluded"})
    ledger.setdefault("snapshots",[]).append({"at":now,"candidateIds":len(candidates),"recruitmentIds":len(eligible),"healthy":healthy}); ledger["snapshots"]=ledger["snapshots"][-120:]; c.write_json(c.LEDGER,ledger)
    report={"generatedAt":now,"policy":"lessoninfo-exact-two-surface-v4","lookbackDays":c.LOOKBACK_DAYS,"base":base,"robotsStatus":robots,"healthy":healthy,"traversalComplete":complete,"sourceReports":reports,"candidateIdCount":len(candidates),"discoveredIdCount":len(eligible),"publishedIdCount":len({j.get('sourceIdentity') for j in jobs if j.get('sourceIdentity')}),"missingAfterCount":len(missing),"missingAfterExamples":missing[:20],"parsedNow":parsed,"rejectedNonRecruitment":len(rejected),"detailErrorCount":len(derr),"detailErrorExamples":derr[:10],"emptyTraversal":empty,"preservedPreviousDataset":not healthy,"errors":errors}
    c.write_json(c.REPORT,report); state=c.read_json(c.STATE,{}); c.write_json(c.STATE,{"updatedAt":now,"healthy":healthy,"lastGoodAt":now if healthy else state.get("lastGoodAt")}); print(json.dumps(report,ensure_ascii=False,indent=2)); return 0 if healthy else 3
if __name__=="__main__": raise SystemExit(main())
