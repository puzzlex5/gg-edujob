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

# Never collapse two different official MirCMS postings merely because their
# title/school text is the same. Re-announcements and round-2 postings often
# reuse titles, while bbsId+nttSn is the stable official identity.
old_key = '''    def key_for(j):
        title = norm(j.get("title", ""))
        school = norm(j.get("school", ""))
        province = j.get("province", "")
        return province + "|" + title if len(title) >= 14 else province + "|" + title + "|" + school
'''
new_key = '''    def key_for(j):
        raw_url = j.get("url") or ""
        try:
            parsed = urlparse(raw_url)
            query = parse_qs(parsed.query)
            ntt = str(j.get("nttSn") or (query.get("nttSn") or [""])[0])
            bbs = str(j.get("bbsId") or (query.get("bbsId") or [""])[0])
            if ntt.isdigit() and bbs.isdigit():
                host = (parsed.hostname or urlparse(j.get("boardUrl") or "").hostname or "").lower()
                return f"mircms|{host}|{bbs}|{ntt}"
        except Exception:
            pass
        title = norm(j.get("title", ""))
        school = norm(j.get("school", ""))
        province = j.get("province", "")
        return province + "|" + title if len(title) >= 14 else province + "|" + title + "|" + school
'''
if old_key in s:
    s = s.replace(old_key, new_key, 1)
elif 'return f"mircms|{host}|{bbs}|{ntt}"' not in s:
    raise RuntimeError('Could not patch repair merge identity')
if 'return f"mircms|{host}|{bbs}|{ntt}"' not in s:
    raise RuntimeError('Stable MirCMS merge identity patch missing')

P.write_text(s, encoding='utf-8')
print('REPAIR_DATA_ID_AND_IDENTITY_PATCH_OK')
