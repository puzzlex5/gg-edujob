#!/usr/bin/env python3
from pathlib import Path

P = Path('scripts/repair_gyeonggi_support.py')
s = P.read_text(encoding='utf-8')
old = '''def detail_url(board_url, row):
    # 1) normal href
    for a in row.find_all("a"):
        href = a.get("href", "") or ""
        if "selectNttInfo.do" in href:
            return urljoin(board_url, href)

    # 2) data-* attributes or javascript/onclick. MirCMS deployments vary.'''
new = '''def detail_url(board_url, row):
    # 1) normal href or current MirCMS data-id identifier.
    for a in row.find_all("a"):
        href = a.get("href", "") or ""
        if "selectNttInfo.do" in href:
            return urljoin(board_url, href)
        data_id = clean(str(a.get("data-id", "")))
        if re.fullmatch(r"\\d{5,10}", data_id):
            return construct_detail(board_url, data_id)

    # 2) data-* attributes or javascript/onclick. MirCMS deployments vary.'''
if old in s:
    s = s.replace(old, new, 1)
elif 'data_id = clean(str(a.get("data-id", "")))' not in s:
    raise RuntimeError('Could not patch repair detail_url')
if 'data_id = clean(str(a.get("data-id", "")))' not in s:
    raise RuntimeError('MirCMS data-id repair patch missing')
P.write_text(s, encoding='utf-8')
print('REPAIR_DATA_ID_PATCH_OK')
