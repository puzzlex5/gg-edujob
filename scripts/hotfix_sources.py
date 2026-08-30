#!/usr/bin/env python3
import json
from pathlib import Path

P = Path('sources.json')
data = json.loads(P.read_text(encoding='utf-8'))
changed = False

for src in data['gyeonggi']['supportOffices']:
    if src['name'] == '구리남양주교육지원청':
        wanted = [
            'https://www.goegn.kr/goegn/na/ntt/selectNttList.do?bbsId=8653&mi=16003',  # 기간제교사
            'https://www.goegn.kr/goegn/na/ntt/selectNttList.do?bbsId=8654&mi=14082',  # 행정실무사
            'https://www.goegn.kr/goegn/na/ntt/selectNttList.do?bbsId=8655&mi=14083',  # 조리실무사
            'https://www.goegn.kr/goegn/na/ntt/selectNttList.do?bbsId=8653&mi=14162',  # 방과후강사
            'https://www.goegn.kr/goegn/na/ntt/selectNttList.do?bbsId=8653&mi=14084',  # 기타
        ]
        if src.get('boardUrls') != wanted:
            src['boardUrls'] = wanted
            changed = True
        break
else:
    raise RuntimeError('구리남양주교육지원청 source entry not found')

if changed:
    P.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('SOURCE_HOTFIX_CHANGED', changed)

# The central portal can return an entire page of IDs already seen in an earlier
# recruitment category.  That is not an end-of-pagination signal: later pages can
# still contain category-specific postings.  The authoritative 90-day crawler
# guards repeated pages by page signature, so keep the fast crawler consistent.
SP = Path('scripts/scrape_jobs.py')
s = SP.read_text(encoding='utf-8')
central_start = s.find('def scrape_gyeonggi_central():')
central_end = s.find('\n\n# -------------------- 경기 교육지원청', central_start)
if central_start < 0 or central_end < 0:
    raise RuntimeError('Gyeonggi central collector section not found')
block = s[central_start:central_end]
central_changed = False

if 'previous_page_ids = None' not in block:
    needle = '    for code, (cat_name, ui_type) in GYEONGGI_CATEGORIES.items():\n        consecutive_old_pages = 0\n'
    replacement = needle + '        previous_page_ids = None\n'
    if needle not in block:
        raise RuntimeError('Gyeonggi central category loop marker not found')
    block = block.replace(needle, replacement, 1)
    central_changed = True

if '            page_ids = []\n            for li in rows:' not in block:
    needle = '            added = 0\n            page_dates = []\n            for li in rows:\n'
    replacement = '            added = 0\n            page_dates = []\n            page_ids = []\n            for li in rows:\n'
    if needle not in block:
        raise RuntimeError('Gyeonggi central page row marker not found')
    block = block.replace(needle, replacement, 1)
    central_changed = True

if '                page_ids.append(pid)\n                if pid in seen_ids:' not in block:
    needle = '                pid = m.group(1)\n                if pid in seen_ids: continue\n'
    replacement = '                pid = m.group(1)\n                page_ids.append(pid)\n                if pid in seen_ids: continue\n'
    if needle not in block:
        raise RuntimeError('Gyeonggi central ID marker not found')
    block = block.replace(needle, replacement, 1)
    central_changed = True

legacy_stop = '            if added == 0: break\n            fully_dated = len(page_dates) == added\n'
hardened_stop = ('            if not page_ids: break\n'
                 '            page_signature = tuple(page_ids)\n'
                 '            if page_signature == previous_page_ids: break\n'
                 '            previous_page_ids = page_signature\n'
                 '            fully_dated = len(page_dates) == added\n')
if hardened_stop not in block:
    if legacy_stop not in block:
        raise RuntimeError('Gyeonggi central premature-stop marker not found')
    block = block.replace(legacy_stop, hardened_stop, 1)
    central_changed = True

if central_changed:
    s = s[:central_start] + block + s[central_end:]
    SP.write_text(s, encoding='utf-8')
print('CENTRAL_PAGINATION_HOTFIX_CHANGED', central_changed)
