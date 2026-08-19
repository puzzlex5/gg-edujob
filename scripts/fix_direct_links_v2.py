#!/usr/bin/env python3
from pathlib import Path

p = Path('scripts/scrape_jobs.py')
s = p.read_text(encoding='utf-8')

# Remove mistakenly injected Seoul POST metadata from Gyeonggi MirCMS jobs.
bad = '''                    "source":office,"checkedSources":[office],"sourceType":"교육지원청 개별 게시판","url":detail,"boardUrl":board,
                    "openMethod":"POST","openUrl":open_url,"openParams":{"job_seq":seq}
'''
good = '''                    "source":office,"checkedSources":[office],"sourceType":"교육지원청 개별 게시판","url":detail,"boardUrl":board
'''
if bad in s:
    s = s.replace(bad, good, 1)

# Add POST metadata only inside the Seoul support-office collector.
marker = 'def scrape_seoul_office(src):'
pos = s.find(marker)
if pos < 0:
    raise RuntimeError('Seoul collector not found')
head, tail = s[:pos], s[pos:]
old = '''                    "source":office,"checkedSources":[office],"sourceType":"교육지원청 개별 게시판","url":detail,"boardUrl":board
'''
new = '''                    "source":office,"checkedSources":[office],"sourceType":"교육지원청 개별 게시판","url":detail,"boardUrl":board,
                    "openMethod":"POST","openUrl":open_url,"openParams":{"job_seq":seq}
'''
if '"openMethod":"POST","openUrl":open_url' not in tail:
    if old not in tail:
        raise RuntimeError('Seoul output record marker not found')
    tail = tail.replace(old, new, 1)

s = head + tail
p.write_text(s, encoding='utf-8')
print('DIRECT LINKS V2 OK')
