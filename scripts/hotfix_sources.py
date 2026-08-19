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
