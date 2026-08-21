#!/usr/bin/env python3
"""Add a conservative row-level nttSn fallback to the deep Gyeonggi crawler.

Some MirCMS category menus (notably Guri-Namyangju bbsId=8653 menu variants)
render valid recruitment rows without a directly parseable title anchor. The normal
anchor parser remains first choice; this fallback only accepts an explicit numeric
nttSn found in the row HTML and constructs the canonical detail URL for the same board.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/complete_support_coverage.py"
s = p.read_text(encoding="utf-8")

old = '''                pick = None\n                detail = ""\n                for a in tr.find_all("a"):\n                    d = detail_from_anchor(r.url, a)\n                    title = clean(a.get_text(" ", strip=True))\n                    if d and len(title) >= 3:\n                        pick, detail = a, d\n                        break\n                if not pick or detail in seen_details:\n                    continue\n                vals = row_vals(tr, hs)\n                row_text = clean(tr.get_text(" ", strip=True))\n                registered = date_norm(first_of(vals, ["등록일", "작성일"]))\n                if not registered:\n                    ds = all_dates(row_text)\n                    registered = ds[-1] if ds else ""\n                title = clean(pick.get_text(" ", strip=True)).replace("새로운 글", "").strip()\n'''
new = '''                pick = None\n                detail = ""\n                title = ""\n                for a in tr.find_all("a"):\n                    d = detail_from_anchor(r.url, a)\n                    anchor_title = clean(a.get_text(" ", strip=True))\n                    if d and len(anchor_title) >= 3:\n                        pick, detail, title = a, d, anchor_title\n                        break\n                vals = row_vals(tr, hs)\n                row_text = clean(tr.get_text(" ", strip=True))\n                if not detail:\n                    # MirCMS category menus can keep the real nttSn only in row-level\n                    # javascript/data attributes while the visible title is plain text.\n                    html = str(tr)\n                    m = re.search(r"nttSn\\s*[=:,'\\\"() ]+\\s*(\\d{4,})", html, re.I)\n                    if not m:\n                        m = re.search(r"(?:nttView|selectNttInfo|goView)\\D+(\\d{4,})", html, re.I)\n                    if m:\n                        bp = urlparse(r.url)\n                        bq = parse_qs(bp.query)\n                        bbs = (bq.get("bbsId") or [""])[0]\n                        if bbs:\n                            params = {"bbsId": bbs, "nttSn": m.group(1)}\n                            for k in ("mi", "clasHmpgId"):\n                                if bq.get(k):\n                                    params[k] = bq[k][0]\n                            detail = urlunparse((bp.scheme, bp.netloc, bp.path.replace("selectNttList.do", "selectNttInfo.do"), "", urlencode(params), ""))\n                            title = first_of(vals, ["제목", "공고명", "채용공고", "학교명"])\n                            if not title:\n                                cells = [clean(td.get_text(" ", strip=True)) for td in tr.find_all("td")]\n                                title = max((x for x in cells if len(x) >= 3 and not DATE_RE.fullmatch(x)), key=len, default="")\n                if not detail or detail in seen_details or len(clean(title)) < 3:\n                    continue\n                registered = date_norm(first_of(vals, ["등록일", "작성일"]))\n                if not registered:\n                    ds = all_dates(row_text)\n                    registered = ds[-1] if ds else ""\n                title = clean(title).replace("새로운 글", "").strip()\n'''

if old in s:
    s = s.replace(old, new, 1)
elif "MirCMS category menus can keep the real nttSn only in row-level" not in s:
    raise SystemExit("Expected Gyeonggi row parser marker not found")

p.write_text(s, encoding="utf-8")
print("Gyeonggi row-level nttSn fallback ready")
