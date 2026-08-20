#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_required(text, old, new, label):
    if old not in text:
        if new in text:
            return text
        raise SystemExit(f'missing expected pattern: {label}')
    return text.replace(old, new)

# 1) Remove shallow pagination caps. Keep date-window stop conditions so we do not
# crawl years of irrelevant history, but allow busy boards to go beyond 7-9 pages.
p = ROOT / 'scripts/scrape_jobs.py'
s = p.read_text(encoding='utf-8')
s = replace_required(s, 'def recent_enough(ds, days=90):', 'def recent_enough(ds, days=180):', 'scraper default retention')
s = replace_required(s, 'for page in range(1, 8):', 'for page in range(1, 61):', 'Gyeonggi support pagination')
s = replace_required(s, 'recent_enough(registered, 90)', 'recent_enough(registered, 180)', 'Gyeonggi support retention')
s = replace_required(s, 'for page in range(1, 10):', 'for page in range(1, 61):', 'Seoul support pagination')
s = replace_required(s, 'recent_enough(registered,90)', 'recent_enough(registered,180)', 'Seoul support retention')
p.write_text(s, encoding='utf-8')

p = ROOT / 'scripts/repair_gyeonggi_support.py'
s = p.read_text(encoding='utf-8')
s = replace_required(s, 'def recent_enough(value, days=150):', 'def recent_enough(value, days=180):', 'repair retention')
s = replace_required(s, 'for page in range(1, 8):', 'for page in range(1, 61):', 'repair pagination')
p.write_text(s, encoding='utf-8')

p = ROOT / 'scripts/resolve_support_links.py'
s = p.read_text(encoding='utf-8')
s = replace_required(s, 'for page in range(1, 9):', 'for page in range(1, 61):', 'detail-link pagination')
p.write_text(s, encoding='utf-8')

# 2) Desktop: left filters and right results become independent wheel-scroll zones.
# Mobile remains a normal single-page scroll.
p = ROOT / 'index.html'
s = p.read_text(encoding='utf-8')
s = replace_required(
    s,
    '.filter-panel{position:sticky;top:12px}.filter-head',
    '.filter-panel{position:sticky;top:12px}.results-panel{min-width:0}.filter-head',
    'results panel base class',
)
s = replace_required(
    s,
    '    @media(max-width:900px){.layout{grid-template-columns:1fr}.filter-panel{position:static}.region-checks',
    '    @media(min-width:901px){.filter-panel,.results-panel{align-self:start;max-height:calc(100vh - 24px);overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable}.results-panel{position:sticky;top:12px;padding-right:5px}.filter-panel{padding-right:12px}}\n    @media(max-width:900px){.layout{grid-template-columns:1fr}.filter-panel,.results-panel{position:static;max-height:none;overflow:visible}.region-checks',
    'desktop independent scroll CSS',
)
s = replace_required(
    s,
    '      <section>\n        <div class="toolbar">',
    '      <section class="results-panel">\n        <div class="toolbar">',
    'results panel markup',
)
# Never render meaningless '- ~ -'.
s = replace_required(
    s,
    "function fmt(s){const d=parseDate(s);if(!d)return s||'-';return `${d.getFullYear()}.${String(d.getMonth()+1).padStart(2,'0')}.${String(d.getDate()).padStart(2,'0')}`}\nfunction esc",
    "function fmt(s){const d=parseDate(s);if(!d)return s||'-';return `${d.getFullYear()}.${String(d.getMonth()+1).padStart(2,'0')}.${String(d.getDate()).padStart(2,'0')}`}\nfunction periodText(start,end){if(!start&&!end)return '원문 확인';if(start&&end)return `${fmt(start)} ~ ${fmt(end)}`;if(start)return `${fmt(start)}부터`;return `${fmt(end)}까지`}\nfunction esc",
    'periodText helper',
)
s = replace_required(
    s,
    '<div><b>접수</b> ${fmt(j.applyStart)} ~ ${fmt(j.applyEnd)}</div><div><b>채용</b> ${fmt(j.workStart)} ~ ${fmt(j.workEnd)}</div>',
    '<div><b>접수</b> ${periodText(j.applyStart,j.applyEnd)}</div><div><b>채용</b> ${periodText(j.workStart,j.workEnd)}</div>',
    'period display',
)
p.write_text(s, encoding='utf-8')

print('Applied: deeper support-office pagination, 180-day window, independent desktop scrolling, safe period display.')
