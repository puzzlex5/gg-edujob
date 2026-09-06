#!/usr/bin/env python3
"""Idempotently point the existing fast UI at the isolated unified search projection."""
from pathlib import Path
import re

p=Path('index.html')
s=p.read_text(encoding='utf-8')
repls={
    '<title>수도권에듀잡 | 서울·경기 교육 채용정보</title>':'<title>수도권에듀잡 | 서울·경기 교육계 구인 통합검색</title>',
    '서울·경기 교육청과 교육지원청 채용공고를 지역·학교급·직종별로 여러 개 선택해 한 번에 찾는 비공식 편의 서비스':'서울·경기 학교·교육청 공식 채용과 검증된 민간 교육·문화예술 구인공고를 한 번에 찾는 통합검색 서비스',
    'placeholder="학교명, 과목, 공고명 검색"':'placeholder="학교·기관·학원·과목·직종·공고명 통합검색"',
    '<div class="t">공식 수집 출처</div>':'<div class="t">수집 출처</div>',
    "fetch('jobs.json',{cache:'no-cache'})":"fetch('unified_jobs.json',{cache:'no-cache'})",
}
for old,new in repls.items():
    if old in s:
        s=s.replace(old,new)
    elif new not in s:
        raise SystemExit(f'required frontend signature missing: {old[:70]}')

# Actually consume the precomputed normalized searchText from unified_jobs.json.
old_hay="const searchHay=j=>norm([j.school,j.title,j.subject,j.region,(j.regions||[]).join(' '),j.type,j.source,j.schoolLevel,j.province].join(' '));"
new_hay="const searchHay=j=>j.searchText||norm([j.school,j.title,j.subject,j.region,(j.regions||[]).join(' '),j.type,j.source,j.schoolLevel,j.province].join(' '));"
if old_hay in s:
    s=s.replace(old_hay,new_hay)
elif new_hay not in s:
    raise SystemExit('searchHay signature changed; refusing unsafe unified search patch')

# Some private postings explicitly cover both Seoul and Gyeonggi. Preserve an array of
# provinces and filter by intersection rather than collapsing a posting to one scalar value.
old_province="function province(j){return j.province||((j.region||'').endsWith('구')?'서울':'경기')}function jobRegions(j){"
new_province="function province(j){return j.province||((j.region||'').endsWith('구')?'서울':'경기')}function jobProvinces(j){const a=Array.isArray(j.provinces)?j.provinces.filter(Boolean):[];return a.length?[...new Set(a)]:[province(j)]}function jobRegions(j){"
if old_province in s:
    s=s.replace(old_province,new_province)
elif new_province not in s:
    raise SystemExit('province helper signature changed; refusing unsafe multi-province patch')

old_filter="if(!matchesSet(state.provinces,province(j)))return false;"
new_filter="if(state.provinces.size&&!jobProvinces(j).some(p=>state.provinces.has(p)))return false;"
if old_filter in s:
    s=s.replace(old_filter,new_filter)
elif new_filter not in s:
    raise SystemExit('province filter signature changed; refusing unsafe multi-province patch')

# Deadline sort policy B keeps actual imminent deadlines first, but no longer buries a
# newly registered official posting solely because its source does not expose applyEnd.
# No source date is synthesized or mutated; the ranking uses applyEnd/registered read-only.
rank_helper="function deadlineSortKey(j){const dd=diffDay(j.applyEnd),rd=parseDate(j.registered),r0=rd?new Date(rd.getFullYear(),rd.getMonth(),rd.getDate()):null,age=r0?Math.floor((today0()-r0)/86400000):null,official=(j.feedKind||'official')==='official';let bucket=4,primary=0;if(dd!==null&&dd>=0&&dd<=3){bucket=0;primary=dd}else if(dd!==null&&dd>=4&&dd<=7){bucket=1;primary=dd}else if(dd===null&&official&&age!==null&&age>=0&&age<=7){bucket=2}else if(dd!==null){bucket=3;primary=dd}return [bucket,primary,-(rd?rd.getTime():0),(j.sourceIdentity||j.id||j.url||'').toString()]}function compareDeadline(x,y){const a=deadlineSortKey(x),b=deadlineSortKey(y);return a[0]-b[0]||a[1]-b[1]||a[2]-b[2]||a[3].localeCompare(b[3])}"
if "function deadlineSortKey(j)" not in s:
    marker="function matchesSet(set,val){return set.size===0||set.has(val)}function filtered(){"
    if marker not in s:
        raise SystemExit('deadline sort insertion point changed; refusing unsafe ranking patch')
    s=s.replace(marker,"function matchesSet(set,val){return set.size===0||set.has(val)}"+rank_helper+"function filtered(){",1)

old_sort="const dx=diffDay(x.applyEnd),dy=diffDay(y.applyEnd);const ax=dx===null?9999:dx,ay=dy===null?9999:dy;return ax-ay||((parseDate(y.registered)||0)-(parseDate(x.registered)||0))"
new_sort="return compareDeadline(x,y)"
if old_sort in s:
    s=s.replace(old_sort,new_sort,1)
elif new_sort not in s or "function deadlineSortKey(j)" not in s:
    raise SystemExit('deadline comparator signature changed; refusing unsafe ranking patch')

marker='<script src="mobile-ui.js?v='
if 'direct-link-guard.js' not in s:
    i=s.find(marker)
    if i<0: raise SystemExit('mobile-ui script marker missing')
    s=s[:i]+'<script src="direct-link-guard.js?v=20260906d" defer></script>\n'+s[i:]
else:
    s,n=re.subn(r'direct-link-guard\.js\?v=[A-Za-z0-9._-]+','direct-link-guard.js?v=20260906d',s,count=1)
    if n!=1: raise SystemExit('direct-link-guard asset reference changed')

if 'unified-ui.js' not in s:
    i=s.find(marker)
    if i<0: raise SystemExit('mobile-ui script marker missing')
    s=s[:i]+'<script src="unified-ui.js?v=20260904a" defer></script>\n'+s[i:]
else:
    s,n=re.subn(r'unified-ui\.js\?v=[A-Za-z0-9._-]+','unified-ui.js?v=20260904a',s,count=1)
    if n!=1: raise SystemExit('unified-ui asset reference changed')

# Keep the data loader compatible with both old and new metadata names.
s=s.replace("if(data.officialSourceCount)$('#countSources').textContent=data.officialSourceCount;","if(data.totalSourceCount||data.officialSourceCount)$('#countSources').textContent=data.totalSourceCount||data.officialSourceCount;")
p.write_text(s,encoding='utf-8')
print('unified frontend patch applied: multi-source search, multi-province filtering, deterministic deadline ranking B, direct-link guard, refreshed UI asset')
