#!/usr/bin/env python3
"""Build a lightweight unified search dataset from independent official/private raw datasets.

Safety model:
- jobs.json remains the canonical official dataset.
- lessoninfo_jobs.json remains the canonical Lessoninfo private dataset.
- unified_jobs.next.json is a disposable search projection only.
- Distinct stable IDs are never merged because titles merely look alike.
- Cross-source automatic merge requires an explicit exact official posting URL/identity captured
  from the private detail page. Semantic title/school/date similarity remains review-only.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")
RESULT_RE = re.compile(r"최종\s*합격|합격자|서류\s*(?:심사|전형)\s*결과|면접\s*대상|선정\s*결과|채용\s*결과|전형\s*결과|인사\s*발령")
ALWAYS_RE = re.compile(r"상시\s*(?:채용|모집)|채용\s*시까지|충원\s*시까지", re.I)
PRIVATE_BANNED_RE = re.compile(r"구직|학원\s*매매|악기\s*(?:판매|매매)|연습실|원생\s*모집|학생\s*모집|레슨생\s*모집|팝니다|삽니다|권리금|임대", re.I)
PROMO_ONLY_RE = re.compile(r"(?:홍보|광고)\s*(?:글|게시글|게시|합니다|드립니다|안내)$", re.I)
INSTRUMENT_RE = re.compile(r"피아노|바이올린|비올라|첼로|플루트|클라리넷|오보에|바순|색소폰|트럼펫|트롬본|호른|튜바|타악기|드럼|기타|우쿨렐레|우크렐레|리코더|칼림바|국악|가야금|해금|대금|사물놀이|합창|성악|작곡|지휘|반주|오케스트라", re.I)


def load(path: str, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        if default is not None:
            return default
        raise


def parse_date(value):
    m = DATE_RE.search(str(value or ""))
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=KST).date()
    except ValueError:
        return None


def norm(value):
    s = unicodedata.normalize("NFKC", str(value or "")).lower()
    s = re.sub(r"[\u200b-\u200d\ufeff]", "", s)
    return re.sub(r"[^0-9a-z가-힣]+", " ", s).strip()


def canonical_url(raw):
    if not raw:
        return ""
    try:
        p = urlparse(str(raw))
        q = parse_qs(p.query, keep_blank_values=True)
        keep = {}
        for key in ("pbancSn", "q_rcrtSn", "rcrtSn", "bbsId", "nttSn", "mi", "job_seq", "id", "wr_no", "seq", "clasHmpgId"):
            if q.get(key):
                keep[key] = q[key][0]
        query = urlencode(sorted(keep.items()))
        return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, "", query, ""))
    except Exception:
        return str(raw).strip()


def official_stable_id(job):
    raw = str(job.get("url") or "")
    try:
        p = urlparse(raw)
        q = parse_qs(p.query)
    except Exception:
        p, q = urlparse(""), {}
    if job.get("sourceType") == "통합게시판":
        pb = str((q.get("pbancSn") or [""])[0])
        if pb.isdigit():
            return f"goe-central:{pb}"
        rid = str((q.get("q_rcrtSn") or q.get("rcrtSn") or [""])[0])
        if rid.isdigit():
            return f"seoul-central:{rid}"
    province = str(job.get("province") or "")
    if province == "서울":
        seq = str((job.get("openParams") or {}).get("job_seq") or (q.get("job_seq") or [""])[0])
        host = (urlparse(str(job.get("openUrl") or raw)).hostname or "").lower()
        if host and seq.isdigit():
            return f"seoul:{host}:{seq}"
    if province == "경기":
        host = (p.hostname or "").lower()
        bbs = str(job.get("bbsId") or (q.get("bbsId") or [""])[0])
        ntt = str(job.get("nttSn") or (q.get("nttSn") or [""])[0])
        if host and bbs.isdigit() and ntt.isdigit():
            return f"mircms:{host}:{bbs}:{ntt}"
    return ""


def latest_official_ids(ledger):
    entries = ledger.get("entries", {}) if isinstance(ledger, dict) else {}
    return {sid for sid, ent in entries.items() if isinstance(ent, dict) and ent.get("presentInLatestOfficialScan") is True}


def official_current(job, protected_ids):
    title = str(job.get("title") or "")
    if RESULT_RE.search(title):
        return False
    end = parse_date(job.get("applyEnd"))
    if end and end < TODAY:
        return False
    reg = parse_date(job.get("registered"))
    if reg and reg > TODAY:
        return False
    if end and end >= TODAY:
        return True
    if ALWAYS_RE.search(title):
        return not reg or reg >= TODAY - timedelta(days=180)
    sid = official_stable_id(job)
    if sid and sid in protected_ids:
        return not reg or reg >= TODAY - timedelta(days=100)
    return bool(reg and reg >= TODAY - timedelta(days=45))


def private_current(job):
    title = str(job.get("title") or "")
    if PRIVATE_BANNED_RE.search(title) or PROMO_ONLY_RE.search(title):
        return False
    if job.get("province") not in {"서울", "경기"}:
        return False
    end = parse_date(job.get("applyEnd"))
    if end and end < TODAY:
        return False
    reg = parse_date(job.get("registered"))
    if reg and reg > TODAY:
        return False
    return True


def region_from(job):
    loc = str(job.get("location") or "")
    province = str(job.get("province") or "")
    if province == "서울":
        m = re.search(r"([가-힣]+구)\b", loc)
        if m:
            return m.group(1)
    if province == "경기":
        m = re.search(r"([가-힣]+(?:시|군))\b", loc)
        if m:
            return m.group(1)
    raw = str(job.get("region") or "")
    if raw not in {"서울", "경기"}:
        return raw
    return ""


def school_from_private(job):
    title = str(job.get("title") or "")
    m = re.search(r"\[([^\]]{2,50})\]", title)
    if m:
        return m.group(1).strip()
    m = re.search(r"([가-힣A-Za-z0-9·]+(?:초등학교|중학교|고등학교|학교|학원|센터|재단|극단|오케스트라|문화원|미술관|박물관|갤러리|예술단|협회))", title)
    return m.group(1).strip() if m else "레슨인포 구인"


def school_level(title, school=""):
    t = f"{school} {title}"
    if re.search(r"유치원|병설유", t): return "유치원"
    if re.search(r"초등학교|\b초\b", t): return "초등학교"
    if re.search(r"중학교|\b중\b", t): return "중학교"
    if re.search(r"고등학교|\b고\b", t): return "고등학교"
    if re.search(r"특수학교", t): return "특수학교"
    if re.search(r"교육지원청|교육청", t): return "교육행정기관"
    return "기타"


def categories(title, source_surface="", source_type=""):
    t = str(title or "")
    out = []
    if source_surface == "afterschool-nulbom" or re.search(r"방과후|늘봄", t): out.append("방과후·늘봄")
    if source_surface == "culture-arts" or re.search(r"문화예술|공연|미술|박물관|미술관|갤러리|극단|예술", t): out.append("문화예술")
    if INSTRUMENT_RE.search(t): out.append("음악·예체능")
    if re.search(r"학원|보습|입시|어학원", t): out.append("학원강사")
    if not out and re.search(r"강사|교사|선생님", t): out.append("강사·교사")
    if not out and source_type == "민간 구인": out.append("기타 민간구인")
    return list(dict.fromkeys(out))


def job_type(title, raw="", private=False):
    t = f"{raw} {title}"
    if re.search(r"자원봉사|봉사자", t): return "자원봉사"
    if re.search(r"정규직|정규\s*채용", t): return "정규채용"
    if re.search(r"교육공무직|기간제근로|조리실무|돌봄전담", t): return "교육공무직/기간제근로자"
    if re.search(r"시간강사|강사|시간제|선생님|지도자|연주자|단원", t): return "시간강사/강사"
    if re.search(r"기간제|교원|교사", t): return "기간제교원"
    return "기타" if private else (raw or "기타")


def project_official(job):
    sid = official_stable_id(job)
    province = str(job.get("province") or "")
    region = region_from(job)
    title = str(job.get("title") or "")
    school = str(job.get("school") or "")
    row = {
        "sourceIdentity": sid or ("official-url:" + canonical_url(job.get("url"))),
        "feedKind": "official",
        "trustLevel": "공식",
        "source": job.get("source") or "공식 채용 게시판",
        "sourceType": job.get("sourceType") or "공식 채용",
        "sourceSurface": "official",
        "sourceSurfaceLabel": "학교·교육청",
        "province": province,
        "region": region or job.get("region") or "",
        "regions": job.get("regions") or ([region] if region else []),
        "school": school,
        "schoolLevel": job.get("schoolLevel") or school_level(title, school),
        "type": job_type(title, str(job.get("type") or "")),
        "title": title,
        "subject": job.get("subject") or "",
        "applyStart": job.get("applyStart") or "",
        "applyEnd": job.get("applyEnd") or "",
        "workStart": job.get("workStart") or "",
        "workEnd": job.get("workEnd") or "",
        "registered": job.get("registered") or "",
        "url": job.get("url") or "",
        "boardUrl": job.get("boardUrl") or "",
        "openMethod": job.get("openMethod") or "",
        "openUrl": job.get("openUrl") or "",
        "openParams": job.get("openParams") or {},
        "nttSn": job.get("nttSn") or "",
        "bbsId": job.get("bbsId") or "",
        "mi": job.get("mi") or "",
        "detailLinkResolved": job.get("detailLinkResolved"),
        "categories": categories(title, "official", str(job.get("sourceType") or "")),
    }
    row["searchText"] = norm(" ".join(map(str, [row["school"], row["title"], row["subject"], row["region"], province, row["source"], row["type"], row["schoolLevel"], " ".join(row["categories"])])))
    return row


def project_private(job):
    title = str(job.get("title") or "")
    province = str(job.get("province") or "")
    region = region_from(job)
    school = school_from_private(job)
    cats = categories(title, str(job.get("sourceSurface") or ""), "민간 구인")
    row = {
        "sourceIdentity": str(job.get("sourceIdentity") or ""),
        "feedKind": "private",
        "trustLevel": "민간",
        "source": "레슨인포",
        "sourceType": "민간 구인",
        "sourceSurface": job.get("sourceSurface") or "",
        "sourceSurfaceLabel": job.get("sourceSurfaceLabel") or "레슨인포",
        "province": province,
        "region": region,
        "regions": [region] if region else [],
        "location": job.get("location") or "",
        "school": school,
        "schoolLevel": school_level(title, school),
        "type": job_type(title, "민간 구인", private=True),
        "title": title,
        "subject": " · ".join(cats),
        "applyStart": "",
        "applyEnd": job.get("applyEnd") or "",
        "workStart": "",
        "workEnd": "",
        "registered": job.get("registered") or "",
        "url": job.get("originalUrl") or job.get("url") or "",
        "originalUrl": job.get("originalUrl") or job.get("url") or "",
        "categories": cats,
        "linkedOfficialUrls": job.get("linkedOfficialUrls") or [],
    }
    row["searchText"] = norm(" ".join(map(str, [school, title, row["subject"], region, province, row["location"], row["source"], row["sourceSurfaceLabel"], row["type"], " ".join(cats)])))
    return row


def official_identity_urls(row):
    """Canonical official detail identities usable for exact cross-source matching."""
    out = set()
    for raw in (row.get("url"), row.get("openUrl")):
        c = canonical_url(raw)
        if c:
            out.add(c)
    seq = str((row.get("openParams") or {}).get("job_seq") or "")
    if seq.isdigit() and row.get("openUrl"):
        try:
            p = urlparse(str(row["openUrl"]))
            out.add(urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, "", urlencode({"job_seq": seq}), "")))
        except Exception:
            pass
    return out


def merge_explicit_official_aliases(official_rows, private_rows):
    """Prefer official representative only for a unique exact explicit official URL match."""
    index = {}
    for row in official_rows:
        for u in official_identity_urls(row):
            index.setdefault(u, []).append(row)

    kept_private = []
    merged = []
    ambiguous = []
    for p in private_rows:
        matches = []
        evidence_urls = []
        for raw in p.get("linkedOfficialUrls") or []:
            c = canonical_url(raw)
            if not c:
                continue
            hits = index.get(c, [])
            if len(hits) == 1:
                matches.append(hits[0])
                evidence_urls.append(c)
            elif len(hits) > 1:
                ambiguous.append({"privateId": p.get("sourceIdentity"), "url": c, "officialMatches": len(hits)})
        unique = {id(x): x for x in matches}
        if len(unique) == 1:
            o = next(iter(unique.values()))
            alias = {
                "source": "레슨인포",
                "sourceIdentity": p.get("sourceIdentity"),
                "sourceSurface": p.get("sourceSurface"),
                "sourceSurfaceLabel": p.get("sourceSurfaceLabel"),
                "privateUrl": p.get("originalUrl") or p.get("url"),
                "evidence": "explicit-exact-official-url",
                "evidenceUrl": evidence_urls[0] if evidence_urls else "",
            }
            existing = {x.get("sourceIdentity") for x in o.setdefault("alsoSeenOn", [])}
            if alias["sourceIdentity"] not in existing:
                o["alsoSeenOn"].append(alias)
            merged.append({"officialId": o.get("sourceIdentity"), "privateId": p.get("sourceIdentity"), "evidenceUrl": alias["evidenceUrl"]})
        else:
            kept_private.append(p)
    return kept_private, merged, ambiguous


def dedupe_strong(rows):
    out = []
    seen_id = set()
    seen_url = {}
    exact_url_groups = []
    for row in rows:
        sid = str(row.get("sourceIdentity") or "")
        if sid and sid in seen_id:
            continue
        url = canonical_url(row.get("url"))
        if url and url in seen_url:
            prior = seen_url[url]
            if prior.get("feedKind") == "official" and row.get("feedKind") == "private":
                prior.setdefault("alsoSeenOn", []).append({"source": row.get("source"), "sourceIdentity": sid, "privateUrl": row.get("originalUrl") or row.get("url"), "evidence": "exact-same-detail-url"})
                exact_url_groups.append([prior.get("sourceIdentity"), sid])
                if sid: seen_id.add(sid)
                continue
            if prior.get("feedKind") == row.get("feedKind"):
                if sid: seen_id.add(sid)
                continue
        out.append(row)
        if sid: seen_id.add(sid)
        if url: seen_url[url] = row
    return out, exact_url_groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--official", default="jobs.json")
    ap.add_argument("--private", default="lessoninfo_jobs.json")
    ap.add_argument("--ledger", default="source_id_ledger.json")
    ap.add_argument("--private-report", default="lessoninfo_reconciliation_report.json")
    ap.add_argument("--output", default="unified_jobs.next.json")
    ap.add_argument("--report", default="unified_search_report.json")
    args = ap.parse_args()

    official_data = load(args.official)
    private_data = load(args.private, {"jobs": []})
    ledger = load(args.ledger, {"entries": {}})
    private_report = load(args.private_report, {})
    official_jobs = official_data.get("jobs", []) if isinstance(official_data, dict) else official_data
    private_jobs = private_data.get("jobs", []) if isinstance(private_data, dict) else private_data
    protected = latest_official_ids(ledger)

    projected_official = [project_official(j) for j in official_jobs if official_current(j, protected)]
    projected_private = [project_private(j) for j in private_jobs if private_current(j)]

    source_surface_counts = {"afterschool-nulbom": 0, "culture-arts": 0}
    for j in projected_private:
        if j.get("sourceSurface") in source_surface_counts:
            source_surface_counts[j["sourceSurface"]] += 1

    remaining_private, explicit_aliases, ambiguous_aliases = merge_explicit_official_aliases(projected_official, projected_private)
    rows, exact_url_groups = dedupe_strong(projected_official + remaining_private)
    rows.sort(key=lambda j: (j.get("registered") or "", j.get("applyEnd") or "9999-12-31", j.get("sourceIdentity") or ""), reverse=True)

    per_feed = {"official": 0, "private": 0}
    for j in rows:
        kind = j.get("feedKind", "official")
        per_feed[kind] = per_feed.get(kind, 0) + 1

    private_source_ok = bool(private_report.get("healthy") and private_report.get("traversalComplete") and private_report.get("missingAfterCount") == 0 and private_report.get("detailErrorCount") == 0)
    official_source_count = int(official_data.get("officialSourceCount", 38))
    payload = {
        "updatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "dataset": "unified-search-v2",
        "officialSourceCount": official_source_count,
        "totalSourceCount": official_source_count + 1,
        "sources": official_data.get("sources", {}),
        "privateSources": {
            "lessoninfo": {
                "name": "레슨인포",
                "ok": private_source_ok,
                "count": len(projected_private),
                "displayedAsPrivate": per_feed.get("private", 0),
                "representedByOfficial": len(explicit_aliases) + len(exact_url_groups),
                "lastVerifiedAt": private_report.get("generatedAt"),
                "missingAfterCount": private_report.get("missingAfterCount"),
                "detailErrorCount": private_report.get("detailErrorCount"),
                "surfaces": source_surface_counts,
            }
        },
        "counts": {
            "total": len(rows),
            **per_feed,
            "lessoninfoSourceOccurrences": len(projected_private),
            "privateSurfaces": source_surface_counts,
        },
        "jobs": rows,
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    report = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "policy": "unified-search-v2-explicit-official-identity-only",
        "canonicalOfficialJobs": len(official_jobs),
        "canonicalPrivateJobs": len(private_jobs),
        "selectedOfficialJobs": len(projected_official),
        "selectedPrivateJobs": len(projected_private),
        "publishedJobs": len(rows),
        "perFeed": per_feed,
        "perPrivateSurface": source_surface_counts,
        "protectedOfficialIds": len(protected),
        "explicitOfficialAliasGroupsMerged": len(explicit_aliases),
        "exactUrlAliasGroupsMerged": len(exact_url_groups),
        "ambiguousExplicitOfficialLinks": len(ambiguous_aliases),
        "explicitAliasExamples": explicit_aliases[:30],
        "ambiguousAliasExamples": ambiguous_aliases[:20],
        "semanticDuplicatePolicy": "review-only-never-auto-delete",
        "privateSourceHealthy": private_source_ok,
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
