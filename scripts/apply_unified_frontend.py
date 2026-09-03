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

# Actually consume the precomputed normalized searchText from unified_jobs.json. This avoids
# rebuilding a long normalized haystack on every filter pass while retaining a fallback for safety.
old_hay="const searchHay=j=>norm([j.school,j.title,j.subject,j.region,(j.regions||[]).join(' '),j.type,j.source,j.schoolLevel,j.province].join(' '));"
new_hay="const searchHay=j=>j.searchText||norm([j.school,j.title,j.subject,j.region,(j.regions||[]).join(' '),j.type,j.source,j.schoolLevel,j.province].join(' '));"
if old_hay in s:
    s=s.replace(old_hay,new_hay)
elif new_hay not in s:
    raise SystemExit('searchHay signature changed; refusing unsafe unified search patch')

marker='<script src="mobile-ui.js?v='
if 'unified-ui.js' not in s:
    i=s.find(marker)
    if i<0: raise SystemExit('mobile-ui script marker missing')
    s=s[:i]+'<script src="unified-ui.js?v=20260903c" defer></script>\n'+s[i:]
else:
    s,n=re.subn(r'unified-ui\.js\?v=[A-Za-z0-9._-]+','unified-ui.js?v=20260903c',s,count=1)
    if n!=1: raise SystemExit('unified-ui asset reference changed')

# Keep the data loader compatible with both old and new metadata names.
s=s.replace("if(data.officialSourceCount)$('#countSources').textContent=data.officialSourceCount;","if(data.totalSourceCount||data.officialSourceCount)$('#countSources').textContent=data.totalSourceCount||data.officialSourceCount;")
p.write_text(s,encoding='utf-8')
print('unified frontend patch applied: lightweight dataset, precomputed searchText, refreshed UI asset')
