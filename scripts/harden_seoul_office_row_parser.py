#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "scrape_jobs.py"

HELPER_MARKER = "def seoul_detail_anchor_for_seq(tr, seq):"

HELPERS = r'''

def seoul_detail_anchor_for_seq(tr, seq):
    """Return the anchor that actually opens this SEN job_seq detail row."""
    seq = str(seq or "")
    if not seq:
        return None
    for a in tr.find_all("a"):
        raw = " ".join((a.get("href", "") or "", a.get("onclick", "") or ""))
        if re.search(rf"fncDetailView\s*\(\s*['\"]?{re.escape(seq)}(?:['\"]|\s|,|\))", raw, re.I):
            return a
        if re.search(rf"job_seq\s*[=,'\"() ]+{re.escape(seq)}(?:\D|$)", raw, re.I):
            return a
        if re.search(rf"JOV11\.do[^\n]*?(?:job_seq\D*)?{re.escape(seq)}(?:\D|$)", raw, re.I):
            return a
    return None


def seoul_row_values(table, tr):
    """Map SEN row cells using the most specific header row with matching width."""
    tds = tr.find_all("td")
    if not tds:
        return {}
    candidates = []
    for hr in table.find_all("tr"):
        if hr is tr:
            break
        ths = hr.find_all("th")
        if not ths:
            continue
        headers = [clean(x.get_text(" ", strip=True)) for x in ths]
        if len(headers) != len(tds):
            continue
        score = sum(any(k in h for k in ("제목", "학교", "기관", "직종", "분야", "등록일", "작성일", "마감")) for h in headers)
        candidates.append((score, headers))
    headers = max(candidates, key=lambda x: x[0])[1] if candidates else table_headers(table)
    vals = {}
    for i, td in enumerate(tds):
        if i < len(headers) and headers[i]:
            vals[headers[i]] = clean(td.get_text(" ", strip=True))
    return vals
'''

OLD_INSERT = '''def scrape_seoul_office(src):\n'''

OLD_VALUES = '''                vals={}\n                for i,td in enumerate(tds):\n                    if i<len(headers) and headers[i]: vals[headers[i]]=clean(td.get_text(" ",strip=True))\n                anchors=[a for a in tr.find_all("a") if clean(a.get_text(" ",strip=True))]\n                title=clean(max((a.get_text(" ",strip=True) for a in anchors),key=len,default=""))\n                if not title: title=first_of(vals,["제목","공고명","분야1","분야"])\n                if len(title)<3 or EXCLUDE_WORDS.search(title): continue\n                registered=date_norm(first_of(vals,["등록일","작성일"]))\n                if not registered:\n                    ds=all_dates(clean(tr.get_text(" ",strip=True))); today_s=NOW.strftime("%Y/%m/%d"); plausible=list(dict.fromkeys(d for d in ds if d and d <= today_s)); registered=plausible[0] if len(plausible)==1 else ""\n                if registered: page_dates.append(registered)\n'''

NEW_VALUES = '''                vals=seoul_row_values(table,tr)\n                detail_anchor=seoul_detail_anchor_for_seq(tr,seq)\n                anchors=[a for a in tr.find_all("a") if clean(a.get_text(" ",strip=True))]\n                title=clean(detail_anchor.get_text(" ",strip=True) if detail_anchor else "")\n                if not title:\n                    title=first_of(vals,["제목","공고명"])\n                if not title:\n                    title=clean(max((a.get_text(" ",strip=True) for a in anchors),key=len,default=""))\n                if len(title)<3 or EXCLUDE_WORDS.search(title): continue\n                registered=date_norm(first_of(vals,["등록일","작성일"]))\n                if registered: page_dates.append(registered)\n'''

OLD_INIT = '''    out, seen = [], set(); raw_rows=0; explicit_empty=False; got_table=False; consecutive_old_pages=0\n'''
NEW_INIT = '''    out, seen = [], set(); raw_rows=0; explicit_empty=False; got_table=False; consecutive_old_pages=0; parse_incomplete=0\n'''

OLD_SCHOOL = '''                school=first_of(vals,["학교명","기관명","작성자"]) or school_from_title(title)\n                raw_level=first_of(vals,["학교급별","학교급","대상"]); raw_type=first_of(vals,["직종","고용형태","구분"])\n'''
NEW_SCHOOL = '''                school=first_of(vals,["학교명","기관명","작성자"]) or school_from_title(title)\n                title_school_collision = bool(school and norm(title) == norm(school)) or norm(title) == norm(office)\n                if not registered or not detail_anchor or title_school_collision:\n                    parse_incomplete += 1\n                    continue\n                raw_level=first_of(vals,["학교급별","학교급","대상"]); raw_type=first_of(vals,["직종","고용형태","구분"])\n'''

OLD_STATE = '''    if raw_rows>0:\n        state,ok,msg="ok",True,"구인 게시판 확인"\n'''
NEW_STATE = '''    if parse_incomplete>0:\n        state,ok,msg="warning",False,f"서울 지원청 행 파싱 불완전 {parse_incomplete}건"\n    elif raw_rows>0:\n        state,ok,msg="ok",True,"구인 게시판 확인"\n'''

OLD_RETURN = '''    return out,{"name":office,"url":board,"boards":[board],"count":len(out),"rawRows":raw_rows,"ok":ok,"state":state,"message":msg}\n'''
NEW_RETURN = '''    return out,{"name":office,"url":board,"boards":[board],"count":len(out),"rawRows":raw_rows,"seoulOfficeParseIncomplete":parse_incomplete,"ok":ok,"state":state,"message":msg}\n'''


def replace_once(text, old, new, label):
    count = text.count(old)
    if count == 0 and new in text:
        return text
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one target, found {count}")
    return text.replace(old, new, 1)


def main():
    text = TARGET.read_text(encoding="utf-8")
    if HELPER_MARKER not in text:
        if OLD_INSERT not in text:
            raise SystemExit("Seoul office scraper insertion point missing")
        text = text.replace(OLD_INSERT, HELPERS + "\n" + OLD_INSERT, 1)
    text = replace_once(text, OLD_INIT, NEW_INIT, "parse-incomplete init")
    text = replace_once(text, OLD_VALUES, NEW_VALUES, "detail title/date parser")
    text = replace_once(text, OLD_SCHOOL, NEW_SCHOOL, "parse completeness gate")
    text = replace_once(text, OLD_STATE, NEW_STATE, "source health gate")
    text = replace_once(text, OLD_RETURN, NEW_RETURN, "status metric")
    compile(text, str(TARGET), "exec")
    TARGET.write_text(text, encoding="utf-8")
    print("Seoul office row parser hardened")


if __name__ == "__main__":
    main()
