#!/usr/bin/env python3
from pathlib import Path
import re


def patch_index():
    p = Path('index.html')
    s = p.read_text(encoding='utf-8')
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
  // 수집기가 nttSn/bbsId를 저장했다면 브라우저에서도 상세주소를 재구성할 수 있다.
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
    if s2 == s:
        return False
    p.write_text(s2, encoding='utf-8')
    return True


def patch_scraper():
    p = Path('scripts/scrape_jobs.py')
    s = p.read_text(encoding='utf-8')
    original = s

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
    elif '"구분","대상","분야","등록일","작성자"' in s and '"작성일"' not in s[s.index('def scrape_seoul_office'):s.index('def dedupe_merge')]:
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

    seoul = s[s.index('def scrape_seoul_office'):s.index('def dedupe_merge')]
    required = [
        'r = get(board, params={"pageIndex":page})',
        '데이터가\\s*없습니다',
        'subject_parts=',
        '"subject":subject',
        '"openMethod":"POST"',
    ]
    missing = [x for x in required if x not in seoul]
    if missing:
        raise RuntimeError('Seoul parser hotfix incomplete: ' + repr(missing))

    if s != original:
        p.write_text(s, encoding='utf-8')
        return True
    return False


if __name__ == '__main__':
    changed = []
    if patch_index(): changed.append('index.html')
    if patch_scraper(): changed.append('scripts/scrape_jobs.py')
    print('HOTFIX_CHANGED', ', '.join(changed) if changed else 'none')
