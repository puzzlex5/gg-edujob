#!/usr/bin/env python3
from pathlib import Path
import json
import re


def patch_index():
    p = Path('index.html')
    s = p.read_text(encoding='utf-8')
    original = s

    pat = re.compile(r'function postingLink\(j\)\{.*?\n\}\n\nfunction card\(j\)\{', re.S)
    replacement = r'''function postingLink(j){
  const board=j.boardUrl||'';
  const raw=j.url||'';

  // 서울 교육지원청은 JOL11 목록에서 job_seq를 POST로 JOV11 상세화면에 넘긴다.
  if(j.openMethod==='POST' && j.openUrl && j.openParams && j.openParams.job_seq){
    const p=new URLSearchParams({url:j.openUrl,job_seq:String(j.openParams.job_seq)});
    return `open-job.html?${p.toString()}`;
  }
  if(province(j)==='서울' && j.sourceType==='교육지원청 개별 게시판'){
    try{
      const u=new URL(raw||'',location.href);
      const seq=u.searchParams.get('job_seq');
      if(seq && /^\d+$/.test(seq) && u.pathname.endsWith('/FUS/JO/JOV11.do')){
        const p=new URLSearchParams({url:`${u.origin}/FUS/JO/JOV11.do`,job_seq:seq});
        return `open-job.html?${p.toString()}`;
      }
    }catch(e){}
    return '';
  }

  // 경기 교육지원청은 반드시 MirCMS 개별 공고(selectNttInfo.do)로 보낸다.
  if(province(j)==='경기' && j.sourceType==='교육지원청 개별 게시판'){
    try{
      if(raw){
        const u=new URL(raw,location.href);
        if(u.pathname.includes('selectNttInfo.do') && u.searchParams.get('nttSn')) return u.href;
      }
      if(j.nttSn && j.bbsId && board){
        const b=new URL(board,location.href);
        const detail=new URL(b.pathname.replace('selectNttList.do','selectNttInfo.do'),b.origin);
        detail.searchParams.set('bbsId',String(j.bbsId));
        if(j.mi) detail.searchParams.set('mi',String(j.mi));
        detail.searchParams.set('nttSn',String(j.nttSn));
        return detail.href;
      }
      if(j.detailLinkResolved===true && raw){
        const u=new URL(raw,location.href);
        if(!u.pathname.includes('selectNttList.do')) return u.href;
      }
    }catch(e){}
    return '';
  }
  return raw||'';
}

function card(j){'''
    s2, n = pat.subn(lambda m: replacement, s, count=1)
    if n != 1:
        raise RuntimeError(f'index postingLink replacement count={n}')
    s = s2

    # Desktop UX: the filter column owns its own vertical scroll.  Wheel events
    # over the result column continue to scroll the normal document/results.
    desktop_rule = '    @media(min-width:901px){.layout{align-items:start}.filter-panel{position:sticky;top:12px;max-height:calc(100vh - 24px);overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable}}\n'
    if '@media(min-width:901px){.layout{align-items:start}.filter-panel{' not in s:
        marker = '    @media(max-width:900px){'
        if marker not in s:
            raise RuntimeError('desktop filter media insertion point not found')
        s = s.replace(marker, desktop_rule + marker, 1)

    mobile_old = '@media(max-width:900px){.layout{grid-template-columns:1fr}.filter-panel{position:static}'
    mobile_new = '@media(max-width:900px){.layout{grid-template-columns:1fr}.filter-panel{position:static;max-height:none;overflow:visible;overscroll-behavior:auto}'
    if mobile_old in s:
        s = s.replace(mobile_old, mobile_new, 1)

    # Avoid presenting a recent/relevant-job count as the lifetime total of an
    # official board.  The crawler intentionally stops after it reaches pages
    # outside the recruitment look-back window.
    s = s.replace('수집 출처 상태 보기', '수집 출처 상태 보기 · 최근 채용 기준')

    if s != original:
        p.write_text(s, encoding='utf-8')
        return True
    return False


def patch_scraper():
    p = Path('scripts/scrape_jobs.py')
    s = p.read_text(encoding='utf-8')
    original = s

    # Gyeonggi MirCMS currently puts the real nttSn in <a data-id="...">.
    old = '''    href = a.get("href", "") or ""
    onclick = a.get("onclick", "") or ""
    if "selectNttInfo.do" in href: return urljoin(board, href)
    raw = href + " " + onclick'''
    new = '''    href = a.get("href", "") or ""
    onclick = a.get("onclick", "") or ""
    if "selectNttInfo.do" in href: return urljoin(board, href)
    data_id = clean(str(a.get("data-id", "")))
    if re.fullmatch(r"\\d{5,10}", data_id):
        p = urlparse(board); q = parse_qs(p.query)
        params = {}
        for k in ("bbsId","mi","clasHmpgId"):
            if q.get(k): params[k] = q[k][0]
        if not params.get("bbsId"): return ""
        params["nttSn"] = data_id
        path = p.path.replace("selectNttList.do", "selectNttInfo.do")
        return urlunparse((p.scheme,p.netloc,path,"",urlencode(params),""))
    raw = href + " " + onclick'''
    if old in s:
        s = s.replace(old, new, 1)

    old = '''        r = post(board,{"pageIndex":str(page),"searchPartPosition":"","searchCondition":"lesson","searchKeyword":""})
        if not r: r = get(board, params={"pageIndex":page})'''
    new = '''        # GET works on more SEN support-office skins; keep POST as a fallback.
        r = get(board, params={"pageIndex":page})
        if not r: r = post(board,{"pageIndex":str(page),"searchPartPosition":"","searchCondition":"lesson","searchKeyword":""})'''
    if old in s:
        s = s.replace(old, new, 1)

    old = '        if re.search(r"총\\s*0\\s*건|전체\\s*0\\s*건|등록된\\s*게시물이\\s*없",txt): explicit_empty=True'
    new = '        if re.search(r"총\\s*0\\s*건|전체\\s*0\\s*건|등록된\\s*게시물이\\s*없|데이터가\\s*없습니다|조회된\\s*데이터가\\s*없|검색된\\s*게시물이\\s*없|등록된\\s*자료가\\s*없",txt): explicit_empty=True'
    if old in s:
        s = s.replace(old, new, 1)
    elif '데이터가\\s*없습니다' in s and '조회된\\s*데이터가\\s*없' not in s:
        s = s.replace('데이터가\\s*없습니다|등록된\\s*자료가\\s*없', '데이터가\\s*없습니다|조회된\\s*데이터가\\s*없|검색된\\s*게시물이\\s*없|등록된\\s*자료가\\s*없', 1)

    old = '            if not any("마감" in h or "직종" in h or "학교" in h for h in headers): continue'
    new = '            if not any(any(k in h for k in ("마감","직종","학교","구분","대상","분야","등록일","작성일","작성자","기관")) for h in headers): continue'
    if old in s:
        s = s.replace(old, new, 1)
    elif '("마감","직종","학교","구분","대상","분야","등록일","작성자")' in s:
        s = s.replace('("마감","직종","학교","구분","대상","분야","등록일","작성자")', '("마감","직종","학교","구분","대상","분야","등록일","작성일","작성자","기관")', 1)

    old = '                if not title: title=first_of(vals,["제목","공고명"])'
    new = '                if not title: title=first_of(vals,["제목","공고명","분야1","분야"])'
    if old in s:
        s = s.replace(old, new, 1)

    old = '''                school=first_of(vals,["학교명","기관명"]) or school_from_title(title)
                raw_level=first_of(vals,["학교급별","학교급"]); raw_type=first_of(vals,["직종","고용형태"])
                apply_end=date_norm(first_of(vals,["마감일","접수마감일"])); region=first_of(vals,["지역"])'''
    new = '''                school=first_of(vals,["학교명","기관명","작성자"]) or school_from_title(title)
                raw_level=first_of(vals,["학교급별","학교급","대상"]); raw_type=first_of(vals,["직종","고용형태","구분"])
                subject_parts=[first_of(vals,["분야1"]),first_of(vals,["분야2"])]
                subject=" / ".join(x for x in subject_parts if x) or first_of(vals,["분야(과목)","분야","과목"])
                apply_end=date_norm(first_of(vals,["마감일","접수마감일"])); region=first_of(vals,["지역"])'''
    if old in s:
        s = s.replace(old, new, 1)

    old = '                    "subject":first_of(vals,["분야(과목)","분야","과목"]),"region":region,"regions":regions,'
    new = '                    "subject":subject,"region":region,"regions":regions,'
    if old in s:
        s = s.replace(old, new, 1)

    # The old seven/nine page ceilings silently truncated busy offices.  Crawl
    # up to 60 pages, while the existing no-new-row / old-date guards stop early.
    s = s.replace('    for page in range(1, 8):\n        u = with_query(board, currPage=page)',
                  '    for page in range(1, 61):\n        u = with_query(board, currPage=page)', 1)
    s = s.replace('    for page in range(1, 10):\n        r = get(board, params={"pageIndex":page})',
                  '    for page in range(1, 61):\n        r = get(board, params={"pageIndex":page})', 1)

    # Keep explicit page coverage in source-health metadata.
    if 'pages_total = sum(x.get("pagesScanned",0) for x in meta)' not in s:
        old = '    raw_total = sum(x["rawRows"] for x in meta)\n    explicit_empty = any(x["explicitEmpty"] for x in meta)'
        new = '    raw_total = sum(x["rawRows"] for x in meta)\n    pages_total = sum(x.get("pagesScanned",0) for x in meta)\n    explicit_empty = any(x["explicitEmpty"] for x in meta)'
        if old in s:
            s = s.replace(old, new, 1)
        s = s.replace('state, ok, msg = "ok", True, f"게시판 {len(boards)}개 확인"',
                      'state, ok, msg = "ok", True, f"게시판 {len(boards)}개 · {pages_total}페이지 확인"', 1)
        s = s.replace('"count":len(all_rows),"rawRows":raw_total,"ok":ok,"state":state,"message":msg}',
                      '"count":len(all_rows),"rawRows":raw_total,"pagesScanned":pages_total,"ok":ok,"state":state,"message":msg}', 1)

    if 'pages_scanned=0' not in s[s.index('def scrape_seoul_office'):s.index('def dedupe_merge')]:
        s = s.replace('out, seen = [], set(); raw_rows=0; explicit_empty=False; got_table=False',
                      'out, seen = [], set(); raw_rows=0; explicit_empty=False; got_table=False; pages_scanned=0', 1)
        s = s.replace('        if not r: break\n        soup=BeautifulSoup(r.text,"html.parser")',
                      '        if not r: break\n        pages_scanned += 1\n        soup=BeautifulSoup(r.text,"html.parser")', 1)
        s = s.replace('return out,{"name":office,"url":board,"boards":[board],"count":len(out),"rawRows":raw_rows,"ok":ok,"state":state,"message":msg}',
                      'return out,{"name":office,"url":board,"boards":[board],"count":len(out),"rawRows":raw_rows,"pagesScanned":pages_scanned,"ok":ok,"state":state,"message":msg}', 1)

    if 'data_id = clean(str(a.get("data-id", "")))' not in s[s.index('def detail_from_anchor'):s.index('def school_from_title')]:
        raise RuntimeError('Gyeonggi data-id detail-link hotfix incomplete')
    if 'for page in range(1, 61):' not in s[s.index('def scrape_mircms_board'):s.index('def scrape_gyeonggi_office')]:
        raise RuntimeError('Gyeonggi full-pagination hotfix incomplete')
    seoul = s[s.index('def scrape_seoul_office'):s.index('def dedupe_merge')]
    required = [
        'r = get(board, params={"pageIndex":page})',
        '데이터가\\s*없습니다',
        'subject_parts=',
        '"subject":subject',
        '"openMethod":"POST"',
        'for page in range(1, 61):',
        'pages_scanned',
    ]
    missing = [x for x in required if x not in seoul]
    if missing:
        raise RuntimeError('Seoul parser hotfix incomplete: ' + repr(missing))

    if s != original:
        p.write_text(s, encoding='utf-8')
        return True
    return False


def patch_repair_collector():
    p = Path('scripts/repair_gyeonggi_support.py')
    s = p.read_text(encoding='utf-8')
    original = s

    # The second-pass verifier used the same seven-page ceiling as the primary
    # collector, which could overwrite a good source status with an undercount.
    s = s.replace('    for page in range(1, 8):', '    for page in range(1, 61):', 1)

    # Include boards discovered by the primary sitemap/menu scan, not only the
    # seed URLs written in sources.json.
    if 'cache = json.loads(cache_path.read_text' not in s:
        s = s.replace('    offices = sources["gyeonggi"]["supportOffices"]\n    session = requests.Session()',
                      '    offices = sources["gyeonggi"]["supportOffices"]\n    cache_path = ROOT / "board_cache.json"\n    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}\n    session = requests.Session()', 1)
        s = s.replace('        boards = list(dict.fromkeys(src.get("boardUrls", [])))',
                      '        boards = list(dict.fromkeys(src.get("boardUrls", []) + cache.get(src["name"], [])))', 1)

    if '"pagesScanned": pages_ok' not in s:
        s = s.replace('state, ok, message = "ok", True, f"실제 게시판 행 {raw_total}건 확인 · 최근 채용 {len(rows)}건"',
                      'state, ok, message = "ok", True, f"실제 게시판 {len(boards)}개 · {pages_ok}페이지 확인 · 최근 채용 {len(rows)}건"', 1)
        s = s.replace('"count": len(rows),\n            "rawRows": raw_total,',
                      '"count": len(rows),\n            "rawRows": raw_total,\n            "pagesScanned": pages_ok,', 1)

    if 'for page in range(1, 61):' not in s[s.index('def fetch_board'):s.index('def merge_jobs')]:
        raise RuntimeError('Gyeonggi repair full-pagination hotfix incomplete')

    if s != original:
        p.write_text(s, encoding='utf-8')
        return True
    return False


if __name__ == '__main__':
    changed = []
    if patch_index(): changed.append('index.html')
    if patch_scraper(): changed.append('scripts/scrape_jobs.py')
    if patch_repair_collector(): changed.append('scripts/repair_gyeonggi_support.py')
    print('HOTFIX_CHANGED', ', '.join(changed) if changed else 'none')
