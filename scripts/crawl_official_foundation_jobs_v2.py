#!/usr/bin/env python3
"""Reliability wrapper for official cultural-foundation recruitment collection."""
from __future__ import annotations
import hashlib, json, re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import crawl_official_foundation_jobs as base
KST=base.KST; OUTPUT=base.OUTPUT; REPORT=base.REPORT; UA=base.UA
SFAC_CAREERLINK_URL="https://sfac.careerlink.kr/"
RETRY=Retry(total=4,connect=4,read=3,status=3,backoff_factor=1.0,status_forcelist=(408,429,500,502,503,504),allowed_methods=frozenset(("GET",)),respect_retry_after_header=True,raise_on_status=False)

def make_session():
    s=requests.Session(); s.mount("https://",HTTPAdapter(max_retries=RETRY)); s.mount("http://",HTTPAdapter(max_retries=RETRY)); return s

def resilient_request(session,url):
    r=session.get(url,timeout=25,headers={"User-Agent":UA,"Cache-Control":"no-cache, no-store, max-age=0","Pragma":"no-cache"},allow_redirects=True); r.raise_for_status()
    if not r.encoding or r.encoding.lower()=="iso-8859-1": r.encoding=r.apparent_encoding or "utf-8"
    return r

def sfac_rows(session,foundation,url):
    r=resilient_request(session,url); soup=BeautifulSoup(r.text,"html.parser"); text=base.normalize_space(soup.get_text(" ",strip=True)); title=""
    for selector in ("h1","h2","h3",".title",".recruit-title"):
        for node in soup.select(selector):
            candidate=base.normalize_space(node.get_text(" ",strip=True))
            if candidate and ("채용" in candidate or "모집" in candidate or "공고" in candidate): title=candidate; break
        if title: break
    if not title:
        m=re.search(r"(서울문화재단[^\n]{0,120}(?:채용|모집|공고)[^\n]{0,120})",text); title=base.normalize_space(m.group(1)) if m else ""
    registered=base.parse_date_text(text[:2500]) or base.detail_registered(soup,None); today=datetime.now(KST).date(); apply_end=base.extract_apply_end(text,registered)
    active=bool(title and registered and registered<=today and (not apply_end or apply_end>=today)) and not base.RESULT_RE.search(title)
    rows=[]
    if active:
        stable=hashlib.sha1(f"{url}|{title}|{registered.isoformat()}".encode()).hexdigest()[:18]; fid=str(foundation.get("id") or "")
        rows.append({"sourceIdentity":f"official-foundation:sfac:{stable}","foundationRegistryId":fid,"foundationName":foundation.get("name") or "서울문화재단","organization":foundation.get("name") or "서울문화재단","source":foundation.get("name") or "서울문화재단","sourceType":"문화재단 공식채용","sourceSurface":"cultural-foundation","sourceSurfaceLabel":"서울문화재단 공식 채용공고","sourceRole":"primary-official","trustLevel":"공식","province":foundation.get("region") or "서울","region":foundation.get("municipality") or "서울특별시","regions":[foundation.get("municipality") or "서울특별시"],"location":" ".join(x for x in [foundation.get("region"),foundation.get("municipality")] if x),"title":title,"registered":base.format_date(registered),"applyEnd":base.format_date(apply_end),"url":r.url,"originalUrl":r.url,"detailUrl":r.url,"boardUrl":url,"detailLinkVerified":True,"detailLinkReason":"official-sfac-current-microsite","transportVerified":True})
    return rows,{"adapter":"sfac-current-microsite","surfacesChecked":[r.url],"discoveredDetailLinks":1 if title else 0,"inspectedDetailLinks":1 if title else 0,"publishedCurrentJobs":len(rows),"active":active,"registered":base.format_date(registered),"applyEnd":base.format_date(apply_end)}

def sfac_careerlink_probe(session):
    r=resilient_request(session,SFAC_CAREERLINK_URL); soup=BeautifulSoup(r.text,"html.parser"); text=base.normalize_space(soup.get_text(" ",strip=True))
    detail_links=[a.get("href") for a in soup.find_all("a",href=True) if any(k in str(a.get("href")) for k in ("recruit","job","apply"))]
    empty_phrase="현재 게시중인 공고가 없습니다" in text or ("0 / 0" in text and "채용공고" in text)
    empty_markup=(r.status_code==200 and "서울문화재단" in text and "채용" in text and not detail_links)
    if not (empty_phrase or empty_markup): raise RuntimeError("sfac.careerlink.kr is not explicitly empty; dedicated current-post parser is required before collection can continue")
    return {"url":r.url,"healthy":True,"currentJobs":0,"explicitEmpty":True,"evidence":"phrase" if empty_phrase else "200-html-no-recruitment-detail-links","role":"secondary-official-contract-surface"}

def main():
    generated=datetime.now(KST).isoformat(timespec="seconds"); foundations=base.effective_foundations(); configured=[x for x in foundations if str(x.get("officialRecruitmentUrl") or "").strip()]; session=make_session(); base.request=resilient_request
    jobs=[]; errors=[]; unsupported=[]; board_results=[]
    for foundation in configured:
        board_url=str(foundation.get("officialRecruitmentUrl") or "").strip(); host=(urlparse(board_url).hostname or "").lower()
        try:
            if host=="nsart.or.kr" or host.endswith(".nsart.or.kr"): found,meta=base.nsart_rows(session,foundation,board_url)
            elif host=="sfac.saramin.co.kr":
                found,meta=sfac_rows(session,foundation,board_url); careerlink=sfac_careerlink_probe(session); meta["surfacesChecked"]=meta.get("surfacesChecked",[])+[careerlink["url"]]; meta["secondarySurfaces"]=[careerlink]
            else: unsupported.append({"foundationRegistryId":foundation.get("id"),"foundationName":foundation.get("name"),"boardUrl":board_url,"reason":"adapter-not-yet-implemented"}); continue
            jobs.extend(found); board_results.append({"foundationRegistryId":foundation.get("id"),"foundationName":foundation.get("name"),"boardUrl":board_url,"healthy":True,**meta})
        except Exception as exc:
            errors.append({"foundationRegistryId":foundation.get("id"),"foundationName":foundation.get("name"),"boardUrl":board_url,"error":f"{type(exc).__name__}: {exc}"}); board_results.append({"foundationRegistryId":foundation.get("id"),"foundationName":foundation.get("name"),"boardUrl":board_url,"healthy":False})
    ids=[str(x.get("sourceIdentity") or "") for x in jobs]; urls=[str(x.get("url") or "") for x in jobs]; duplicate_ids=sorted({x for x in ids if x and ids.count(x)>1}); duplicate_urls=sorted({x for x in urls if x and urls.count(x)>1})
    if duplicate_ids or duplicate_urls: errors.append({"error":"duplicate-official-identities","ids":duplicate_ids,"urls":duplicate_urls})
    jobs.sort(key=lambda x:(str(x.get("registered") or ""),str(x.get("sourceIdentity") or "")),reverse=True); healthy=not errors and not unsupported
    OUTPUT.write_text(json.dumps({"generatedAt":generated,"sourceRole":"primary-official","jobs":jobs},ensure_ascii=False,indent=2),encoding="utf-8")
    REPORT.write_text(json.dumps({"generatedAt":generated,"policy":"official-foundation-primary-fail-closed-v5-retry-sfac-careerlink","healthy":healthy,"registryInstitutions":len(foundations),"officialBoardsConfigured":len(configured),"supportedBoardsChecked":len(board_results),"unsupportedConfiguredBoards":unsupported,"jobs":len(jobs),"errors":errors,"boards":board_results},ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(json.loads(REPORT.read_text(encoding="utf-8")),ensure_ascii=False,indent=2)); return 0 if healthy else 2
if __name__=="__main__": raise SystemExit(main())
