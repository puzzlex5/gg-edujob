#!/usr/bin/env python3
"""Cross-check publication-effective private signals against official data.

This is an omission sensor, not a fuzzy deduplicator.
- Strong automatic matches require an exact official URL explicitly linked by a private row.
- Title/school/date similarity is review-only and never deletes or merges records.
- School/afterschool private postings with no official candidate are surfaced as possible official gaps.
- Degraded or publication-disabled private sources are excluded from gap generation to avoid false alarms.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from private_source_registry import PRIVATE_SOURCES, publication_enabled, source_health

KST=timezone(timedelta(hours=9))
DATE_RE=re.compile(r"(20\d{2})\D+(\d{1,2})\D+(\d{1,2})")
SCHOOL_RE=re.compile(r"([가-힣A-Za-z0-9·]+(?:초등학교|중학교|고등학교|학교))")
SCHOOL_SIGNAL=re.compile(r"초등학교|중학교|고등학교|학교|방과후|늘봄|특성화\s*강사|예술교육",re.I)
STOP={"채용","모집","공고","강사","교사","선생님","구함","구인","기간제","학교","2026년","2026"}
ALLOWED_PROVINCES={"서울","경기"}


def load(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:return default

def rows_from(data):
    if isinstance(data,list):return data
    if isinstance(data,dict):return data.get('jobs',[])
    return []

def norm(s):
    s=unicodedata.normalize('NFKC',str(s or '')).lower()
    return re.sub(r'[^0-9a-z가-힣]+',' ',s).strip()

def date(v):
    m=DATE_RE.search(str(v or ''))
    if not m:return None
    try:return datetime(int(m.group(1)),int(m.group(2)),int(m.group(3)),tzinfo=KST).date()
    except ValueError:return None

def canon(raw):
    if not raw:return ''
    try:
        p=urlparse(str(raw));q=parse_qs(p.query,keep_blank_values=True);keep={}
        for k in ('pbancSn','q_rcrtSn','rcrtSn','bbsId','nttSn','mi','job_seq','seq','clasHmpgId'):
            if q.get(k):keep[k]=q[k][0]
        return urlunparse((p.scheme.lower(),p.netloc.lower(),p.path,'',urlencode(sorted(keep.items())),''))
    except Exception:return str(raw).strip()

def official_urls(j):
    out=set()
    for raw in (j.get('url'),j.get('openUrl'),j.get('boardUrl')):
        c=canon(raw)
        if c:out.add(c)
    seq=str((j.get('openParams') or {}).get('job_seq') or '')
    if seq.isdigit() and j.get('openUrl'):
        try:
            p=urlparse(j['openUrl']);out.add(urlunparse((p.scheme.lower(),p.netloc.lower(),p.path,'',urlencode({'job_seq':seq}),'')))
        except Exception:pass
    return out

def tokens(title):
    return {x for x in norm(title).split() if len(x)>=2 and x not in STOP}

def similarity(a,b):
    aa,bb=tokens(a),tokens(b)
    if not aa or not bb:return 0.0
    return len(aa&bb)/len(aa|bb)

def school_name(j):
    for v in (j.get('school'),j.get('title')):
        m=SCHOOL_RE.search(str(v or ''))
        if m:return norm(m.group(1))
    return ''

def provinces(j):
    ps={str(x) for x in (j.get('provinces') or []) if str(x)}
    if ps:return ps & ALLOWED_PROVINCES
    p=str(j.get('province') or '')
    return {p} if p in ALLOWED_PROVINCES else set()

def main():
    official=load('jobs.json',{})
    oj=rows_from(official)
    url_index={}
    for j in oj:
        for u in official_urls(j):url_index.setdefault(u,[]).append(j)

    by_province={'서울':[],'경기':[]}
    for j in oj:
        if j.get('province') in by_province:by_province[j['province']].append(j)

    private_rows=[]
    source_meta={}
    per_source={}
    for spec in PRIVATE_SOURCES:
        jobs=rows_from(load(spec['jobs'],[]))
        rep=load(spec['report'],{})
        drep=load(spec['detail_report'],{}) if spec.get('detail_report') else None
        configured=publication_enabled(rep)
        healthy=source_health(spec,rep,drep)
        effective=configured and healthy
        source_meta[spec['key']]={
            'name':spec['name'],
            'configuredPublicationEnabled':configured,
            'publicationEnabled':effective,
            'degraded':configured and not healthy,
            'ok':healthy,
            'canonicalCount':len(jobs),
        }
        per_source[spec['key']]={'jobs':0,'strongOfficialMatches':0,'semanticReviewCandidates':0,'officialGapCandidates':0}
        if not effective:
            continue
        for raw in jobs:
            row=dict(raw)
            row['_privateSourceKey']=spec['key']
            row['_privateSourceName']=spec['name']
            private_rows.append(row)
            per_source[spec['key']]['jobs']+=1

    strong=[];review=[];gaps=[]
    for p in private_rows:
        skey=p['_privateSourceKey'];sname=p['_privateSourceName']
        pid=p.get('sourceIdentity')
        linked=[canon(u) for u in (p.get('linkedOfficialUrls') or []) if canon(u)]
        sm=[]
        for u in linked:
            for o in url_index.get(u,[]):
                sm.append({'officialSource':o.get('source'),'officialTitle':o.get('title'),'officialUrl':o.get('url') or o.get('openUrl'),'evidence':'explicit-exact-official-url'})
        if sm:
            strong.append({'privateSource':skey,'privateSourceName':sname,'privateId':pid,'privateTitle':p.get('title'),'matches':sm})
            per_source[skey]['strongOfficialMatches']+=1
            continue

        if not (p.get('sourceSurface')=='afterschool-nulbom' or SCHOOL_SIGNAL.search(str(p.get('title') or ''))):
            continue
        pprovs=provinces(p);preg=date(p.get('registered'));pschool=school_name(p);cands=[];seen=set()
        for pprov in pprovs:
            for o in by_province.get(pprov,[]):
                oreg=date(o.get('registered'))
                if preg and oreg and abs((preg-oreg).days)>10:continue
                oschool=school_name(o);sim=similarity(p.get('title'),o.get('title'))
                same_school=bool(pschool and oschool and pschool==oschool)
                exact_title=bool(norm(p.get('title')) and norm(p.get('title'))==norm(o.get('title')))
                if exact_title or (same_school and sim>=0.45) or sim>=0.78:
                    oid=str(o.get('sourceIdentity') or o.get('url') or o.get('openUrl') or '')
                    if oid in seen:continue
                    seen.add(oid)
                    cands.append({'officialSource':o.get('source'),'officialTitle':o.get('title'),'officialUrl':o.get('url') or o.get('openUrl'),'registered':o.get('registered'),'sameSchool':same_school,'titleSimilarity':round(sim,3),'evidence':'semantic-review-only'})
        if cands:
            cands.sort(key=lambda x:(x['sameSchool'],x['titleSimilarity']),reverse=True)
            review.append({'privateSource':skey,'privateSourceName':sname,'privateId':pid,'privateTitle':p.get('title'),'provinces':sorted(pprovs),'registered':p.get('registered'),'candidates':cands[:5]})
            per_source[skey]['semanticReviewCandidates']+=1
        else:
            gaps.append({'privateSource':skey,'privateSourceName':sname,'privateId':pid,'privateTitle':p.get('title'),'provinces':sorted(pprovs),'registered':p.get('registered'),'url':p.get('originalUrl') or p.get('url'),'reason':'school-or-afterschool-private-post-with-no-official-candidate'})
            per_source[skey]['officialGapCandidates']+=1

    report={
        'generatedAt':datetime.now(KST).isoformat(timespec='seconds'),
        'policy':'explicit-url-strong; semantic-review-only; no-title-based-delete; publication-effective-private-only',
        'privateJobs':len(private_rows),'officialJobs':len(oj),
        'privateSources':source_meta,
        'perSource':per_source,
        'strongOfficialMatches':len(strong),
        'semanticReviewCandidates':len(review),
        'officialGapCandidates':len(gaps),
        'strongMatches':strong[:150],
        'reviewCandidates':review[:200],
        'gapCandidates':gaps[:300],
    }
    Path('unified_crosscheck_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'strongMatches','reviewCandidates','gapCandidates'}},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
