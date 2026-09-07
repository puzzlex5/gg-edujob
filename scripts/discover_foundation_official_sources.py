#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
BASE = "https://job.cleaneye.go.kr"
PAGE = BASE + "/user/ypRecruitment.do"
API = BASE + "/user/selectYpRecruitment.do"
DETAIL = BASE + "/user/ypCareersData.do"
REGISTRY = Path("cultural_foundation_registry.json")
OUT = Path("foundation_official_source_candidates.json")
REPORT = Path("foundation_official_source_discovery_report.json")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36"
BAD_HOSTS = {
    "job.cleaneye.go.kr", "www.cleaneye.go.kr", "cleaneye.go.kr",
    "www.facebook.com", "facebook.com", "www.instagram.com", "instagram.com",
    "www.youtube.com", "youtube.com", "youtu.be", "blog.naver.com",
}
LINK_WORDS = ("채용", "인재", "직원", "구인", "공고", "공지", "알림", "소식")
HOME_WORDS = ("홈페이지", "기관홈페이지", "사이트", "바로가기")


def norm(s: str) -> str:
    s = re.sub(r"\(\s*재\s*\)|재단법인", "", str(s or ""), flags=re.I)
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", s).lower()


def exact_ent_match(found: str, foundation: dict) -> bool:
    targets = {norm(foundation.get("name")), *(norm(x) for x in foundation.get("aliases") or [])}
    targets.discard("")
    return norm(found) in targets


def clean_url(raw: str) -> str:
    try:
        p = urlparse(raw)
        if p.scheme not in {"http", "https"} or not p.hostname:
            return ""
        return urlunparse((p.scheme, p.netloc, p.path or "/", "", p.query, ""))
    except Exception:
        return ""


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9", "Referer": PAGE})
    s.get(PAGE, timeout=30).raise_for_status()
    return s


def latest_cleaneye_row(s: requests.Session, foundation: dict) -> dict | None:
    name = str(foundation["name"])
    r = s.post(API, data={"pageIndex":"1","pageUnit":"10","pageSize":"10","status":"","entName":name,"searchKeyword":name}, timeout=30, headers={"X-Requested-With":"XMLHttpRequest"})
    r.raise_for_status()
    rows = [x for x in (r.json().get("list") or []) if exact_ent_match(x.get("entName", ""), foundation)]
    if not rows:
        return None
    rows.sort(key=lambda x: (str(x.get("pubDate") or ""), str(x.get("entSeq") or "")), reverse=True)
    return rows[0]


def cleaneye_external_links(s: requests.Session, row: dict) -> tuple[str, list[dict]]:
    detail = DETAIL + "?" + urlencode({"empyear":row.get("empyear",""),"entSeq":row.get("entSeq",""),"ypEntId":row.get("ypEntId","")})
    r = s.get(detail, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = clean_url(urljoin(detail, a.get("href")))
        if not href:
            continue
        host = (urlparse(href).hostname or "").lower()
        if host in BAD_HOSTS or host.endswith("cleaneye.go.kr"):
            continue
        if href in seen:
            continue
        seen.add(href)
        text = " ".join(a.get_text(" ", strip=True).split())[:200]
        score = 0
        lo = (text + " " + href).lower()
        if any(w in text for w in LINK_WORDS): score += 5
        if any(w in text for w in HOME_WORDS): score += 2
        if any(w in lo for w in ("recruit", "career", "job", "notice", "board")): score += 3
        out.append({"url":href,"text":text,"score":score,"source":"cleaneye-latest-detail"})
    out.sort(key=lambda x: (x["score"], len(x["url"])), reverse=True)
    return detail, out[:30]


def host_root(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/" if p.scheme and p.netloc else ""


def foundation_identity_in_text(foundation: dict, text: str) -> bool:
    n = norm(text)
    variants = [foundation.get("name"), *(foundation.get("aliases") or [])]
    return any(norm(v) and norm(v) in n for v in variants)


def inspect_candidate(s: requests.Session, foundation: dict, candidate: dict) -> dict:
    url = candidate["url"]
    result = dict(candidate)
    try:
        r = s.get(url, timeout=25, allow_redirects=True, headers={"X-Requested-With":""})
        result["status"] = r.status_code
        result["finalUrl"] = clean_url(r.url)
        result["contentType"] = r.headers.get("content-type", "")
        if r.status_code != 200 or "html" not in result["contentType"].lower():
            result["verified"] = False
            result["reason"] = "non-html-or-non-200"
            return result
        soup = BeautifulSoup(r.text, "html.parser")
        body = " ".join(soup.get_text(" ", strip=True).split())[:30000]
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        identity = foundation_identity_in_text(foundation, title + " " + body[:8000])
        result["pageTitle"] = title[:300]
        result["identityMatch"] = identity
        result["verified"] = bool(identity)
        result["reason"] = "foundation-identity-match" if identity else "foundation-identity-missing"
        if identity:
            links = []
            for a in soup.find_all("a", href=True):
                text = " ".join(a.get_text(" ", strip=True).split())[:200]
                href = clean_url(urljoin(r.url, a.get("href")))
                if not href or (urlparse(href).hostname or "").lower() != (urlparse(r.url).hostname or "").lower():
                    continue
                if any(w in text for w in LINK_WORDS) or any(w in href.lower() for w in ("recruit", "career", "job", "notice", "board")):
                    score = sum(3 for w in LINK_WORDS if w in text)
                    if any(w in href.lower() for w in ("recruit", "career", "job")): score += 5
                    links.append({"text":text,"url":href,"score":score})
            dedup = {x["url"]: x for x in sorted(links, key=lambda x:x["score"], reverse=True)}
            result["sameHostRecruitmentCandidates"] = list(dedup.values())[:30]
        return result
    except Exception as exc:
        result["verified"] = False
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result


def main() -> int:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    foundations = [x for x in registry.get("institutions", []) if x.get("enabled") is not False]
    s = session()
    findings = []
    errors = []
    for foundation in foundations:
        item = {"foundationRegistryId":foundation["id"],"foundationName":foundation["name"],"region":foundation["region"],"municipality":foundation["municipality"]}
        candidates = []
        if foundation.get("homepage"):
            candidates.append({"url":clean_url(foundation["homepage"]),"text":"registry homepage","score":20,"source":"registry"})
        if foundation.get("officialRecruitmentUrl"):
            candidates.append({"url":clean_url(foundation["officialRecruitmentUrl"]),"text":"registry recruitment","score":30,"source":"registry"})
        try:
            row = latest_cleaneye_row(s, foundation)
            if row:
                detail, links = cleaneye_external_links(s, row)
                item["latestCleaneye"] = {"detailUrl":detail,"title":row.get("entTitle"),"pubDate":row.get("pubDate"),"pubEndDate":row.get("pubEndDate")}
                candidates.extend(links)
        except Exception as exc:
            item["cleaneyeDiscoveryError"] = f"{type(exc).__name__}: {exc}"
        unique = {}
        for c in candidates:
            if c.get("url") and c["url"] not in unique:
                unique[c["url"]] = c
        verified = [inspect_candidate(s, foundation, c) for c in sorted(unique.values(), key=lambda x:x.get("score",0), reverse=True)[:12]]
        item["candidates"] = verified
        good = [x for x in verified if x.get("verified")]
        item["verifiedCandidateCount"] = len(good)
        homepage = next((x.get("finalUrl") or x["url"] for x in good if x.get("source") == "registry" and "homepage" in x.get("text", "")), "")
        if not homepage and good:
            homepage = host_root(good[0].get("finalUrl") or good[0]["url"])
        item["discoveredHomepage"] = homepage
        board_candidates = []
        for g in good:
            board_candidates.extend(g.get("sameHostRecruitmentCandidates") or [])
            if g.get("score",0) >= 5 and any(w in ((g.get("text") or "") + " " + (g.get("url") or "")) for w in LINK_WORDS):
                board_candidates.append({"text":g.get("text"),"url":g.get("finalUrl") or g.get("url"),"score":g.get("score",0)})
        board_dedup = {x["url"]:x for x in sorted(board_candidates, key=lambda x:x.get("score",0), reverse=True) if x.get("url")}
        item["recruitmentBoardCandidates"] = list(board_dedup.values())[:20]
        findings.append(item)

    generated = datetime.now(KST).isoformat(timespec="seconds")
    OUT.write_text(json.dumps({"generatedAt":generated,"institutions":findings}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    verified_homepages = sum(1 for x in findings if x.get("discoveredHomepage"))
    board_candidates = sum(1 for x in findings if x.get("recruitmentBoardCandidates"))
    report = {"generatedAt":generated,"healthy":len(findings)==len(foundations),"registryInstitutions":len(foundations),"processed":len(findings),"verifiedHomepageCandidates":verified_homepages,"institutionsWithBoardCandidates":board_candidates,"errors":errors}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["healthy"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
