#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import build_unified_search as base
from private_source_registry import PRIVATE_SOURCES, lessoninfo_culture_failclosed, publication_enabled, source_health
from source_registry import official_source_count

KST = timezone(timedelta(hours=9))
_BASE_CANONICAL_URL = base.canonical_url


def canonical_url_multi(raw):
    if not raw: return ""
    try:
        p=urlparse(str(raw)); q=parse_qs(p.query,keep_blank_values=True); host=(p.hostname or "").lower()
        rec=str((q.get("rec_idx") or [""])[0])
        if rec.isdigit() and host.endswith("artmore.kr"):
            return urlunparse((p.scheme.lower(),p.netloc.lower(),p.path,"",urlencode({"rec_idx":rec}),""))
        if host=="job.cleaneye.go.kr" and p.path.endswith("/user/ypCareersData.do"):
            empyear=str((q.get("empyear") or [""])[0]); entseq=str((q.get("entSeq") or [""])[0]); entid=str((q.get("ypEntId") or [""])[0])
            if empyear.isdigit() and entseq.isdigit() and entid:
                return urlunparse((p.scheme.lower(),p.netloc.lower(),p.path,"",urlencode({"empyear":empyear,"entSeq":entseq,"ypEntId":entid}),""))
        idx=str((q.get("idx") or [""])[0])
        if idx.isdigit() and host.endswith("seekle.or.kr") and p.path.endswith("/sub07/sub01.php"):
            return urlunparse((p.scheme.lower(),p.netloc.lower(),p.path,"",urlencode({"idx":idx,"ptype":"view"}),""))
        if idx.isdigit() and host.endswith("boramyc.or.kr") and p.path.endswith("/sub06/sub01.php"):
            return urlunparse((p.scheme.lower(),p.netloc.lower(),p.path,"",urlencode({"idx":idx,"ptype":"view"}),""))
    except Exception:
        pass
    return _BASE_CANONICAL_URL(raw)

base.canonical_url=canonical_url_multi


def load(path,default=None):
    try: return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception: return default

def rows_from(data):
    if isinstance(data,list): return data
    if isinstance(data,dict): return data.get("jobs",[])
    return []
def project_private_generic(job,source_name):
    row=base.project_private(job)
    row["source"]=str(job.get("source") or source_name)
    row["sourceSurfaceLabel"]=str(job.get("sourceSurfaceLabel") or source_name)
    row["sourceRole"]=str(job.get("sourceRole") or "supplemental")
    if row.get("school")=="레슨인포 구인" and source_name!="레슨인포": row["school"]=source_name+" 구인"
    explicit_provinces=[str(x) for x in (job.get("provinces") or []) if str(x)]
    if explicit_provinces:
        row["provinces"]=list(dict.fromkeys(explicit_provinces))
        if row.get("province") not in row["provinces"]: row["province"]=row["provinces"][0]
    else: row["provinces"]=[row.get("province")] if row.get("province") else []
    explicit_regions=[str(x) for x in (job.get("regions") or []) if str(x)]
    if explicit_regions:
        row["regions"]=list(dict.fromkeys(explicit_regions)); row["region"]=str(job.get("region") or row["region"] or row["regions"][0])

    if source_name=="클린아이 잡플러스":
        row["trustLevel"]="공공"
        row["sourceType"]="공공기관 통합채용"
        row["sourceRole"]="public-foundation-safety-source"
        row["sourceSurface"]="cultural-foundation"
        row["school"]=str(job.get("foundationName") or job.get("organization") or "문화재단")
        row["schoolLevel"]="문화재단"
        row["categories"]=["문화예술","공공 문화재단"]
        row["subject"]="문화예술 · 공공 문화재단"
        row["foundationRegistryId"]=str(job.get("foundationRegistryId") or "")
        row["cleaneyeUrl"]=str(job.get("cleaneyeUrl") or row.get("url") or "")

    # Lessoninfo culture is discovery-only for authority/deadlines. It may still expose an
    # individually verified destination, but it is never treated as proof that foundation
    # coverage is complete; CleanEye/ArtMore/official-board evidence outranks it.
    if source_name=="레슨인포" and str(job.get("sourceSurface") or "")=="culture-arts":
        row["sourceRole"]="discovery-only"
        row["detailLinkVerified"]=job.get("detailLinkVerified")
        row["detailLinkReason"]=str(job.get("detailLinkReason") or job.get("detailLinkVerificationReason") or "")
        row["verifiedAt"]=str(job.get("verifiedAt") or "")
        row["verifiedUrl"]=str(job.get("verifiedUrl") or "")
        row["resolvedUrlType"]=str(job.get("resolvedUrlType") or "")
        row["detailUrl"]=str(job.get("detailUrl") or job.get("unverifiedDetailUrl") or "")
        if lessoninfo_culture_failclosed(job):
            row["url"]=""; row["originalUrl"]=""; row["detailLinkVerified"]=False
        else:
            verified_url=str(job.get("verifiedUrl") or ""); row["url"]=verified_url; row["originalUrl"]=verified_url; row["detailLinkVerified"]=True

    row["searchText"]=base.norm(" ".join(map(str,[row.get("school"),row.get("title"),row.get("subject"),row.get("region")," ".join(row.get("regions") or []),row.get("province")," ".join(row.get("provinces") or []),row.get("location"),row.get("source"),row.get("sourceSurfaceLabel"),row.get("type")," ".join(row.get("categories") or [])])))
    return row


def project_foundation_official(job):
    row=project_private_generic(job,str(job.get("foundationName") or job.get("source") or "문화재단 공식채용"))
    foundation=str(job.get("foundationName") or job.get("organization") or row.get("school") or "문화재단")
    row["feedKind"]="official"
    row["trustLevel"]="공식"
    row["source"]=str(job.get("source") or foundation)
    row["sourceType"]="문화재단 공식채용"
    row["sourceSurface"]="cultural-foundation"
    row["sourceSurfaceLabel"]=str(job.get("sourceSurfaceLabel") or f"{foundation} 공식 채용공고")
    row["sourceRole"]="primary-official"
    row["school"]=foundation
    row["schoolLevel"]="문화재단"
    row["categories"]=["문화예술","공공 문화재단"]
    row["subject"]="문화예술 · 공공 문화재단"
    row["foundationRegistryId"]=str(job.get("foundationRegistryId") or "")
    row["boardUrl"]=str(job.get("boardUrl") or "")
    row["detailLinkVerified"]=job.get("detailLinkVerified") is True
    row["detailLinkReason"]=str(job.get("detailLinkReason") or "official-foundation-detail")
    row["transportVerified"]=job.get("transportVerified") is True
    row["originalUrl"]=str(job.get("originalUrl") or job.get("url") or row.get("url") or "")
    row["searchText"]=base.norm(" ".join(map(str,[row.get("school"),row.get("title"),row.get("subject"),row.get("region")," ".join(row.get("regions") or []),row.get("province")," ".join(row.get("provinces") or []),row.get("location"),row.get("source"),row.get("sourceSurfaceLabel"),row.get("type")," ".join(row.get("categories") or [])])))
    return row


def dedupe_multi_source(rows):
    out=[]; seen_id=set(); by_url={}; exact_url_groups=[]
    for row in rows:
        sid=str(row.get("sourceIdentity") or "")
        if sid and sid in seen_id: continue
        url=base.canonical_url(row.get("url")); prior_rows=by_url.get(url,[]) if url else []
        official_prior=next((p for p in prior_rows if p.get("feedKind")=="official"),None)
        if official_prior is not None and row.get("feedKind")=="private":
            official_prior.setdefault("alsoSeenOn",[]).append({"source":row.get("source"),"sourceIdentity":sid,"privateUrl":row.get("originalUrl") or row.get("url"),"evidence":"exact-same-detail-url"})
            exact_url_groups.append([official_prior.get("sourceIdentity"),sid])
            if sid: seen_id.add(sid)
            continue
        same_source_prior=next((p for p in prior_rows if p.get("feedKind")==row.get("feedKind") and p.get("source")==row.get("source")),None)
        if same_source_prior is not None:
            if sid: seen_id.add(sid)
            continue
        out.append(row)
        if sid: seen_id.add(sid)
        if url: by_url.setdefault(url,[]).append(row)
    return out,exact_url_groups


def main():
    official_data=load("jobs.json",{}); official_jobs=rows_from(official_data)
    foundation_official_data=load("official_foundation_jobs.json",{}); foundation_official_jobs=rows_from(foundation_official_data)
    ledger=load("source_id_ledger.json",{"entries":{}}); protected=base.latest_official_ids(ledger)
    projected_canonical_official=[base.project_official(j) for j in official_jobs if base.official_current(j,protected)]
    projected_foundation_official=[project_foundation_official(j) for j in foundation_official_jobs if base.private_current(j)]
    projected_official=projected_canonical_official+projected_foundation_official
    all_private=[]; private_meta={}; canonical_private_total=0; enabled_private_sources=0; degraded_private_sources=[]
    for spec in PRIVATE_SOURCES:
        pdata=load(spec["jobs"],[]); preport=load(spec["report"],{}); dreport=load(spec["detail_report"],{}) if spec.get("detail_report") else None
        jobs=rows_from(pdata); projected=[project_private_generic(j,spec["name"]) for j in jobs if base.private_current(j)]
        configured_enabled=publication_enabled(preport); healthy=source_health(spec,preport,dreport); effective_enabled=configured_enabled and healthy
        if effective_enabled:
            enabled_private_sources+=1; canonical_private_total+=len(jobs); all_private.extend(projected)
        elif configured_enabled and not healthy: degraded_private_sources.append(spec["key"])
        private_meta[spec["key"]]={"name":spec["name"],"configuredPublicationEnabled":configured_enabled,"publicationEnabled":effective_enabled,"degraded":configured_enabled and not healthy,"ok":healthy,"candidateCount":len(projected),"count":len(projected) if effective_enabled else 0,"lastVerifiedAt":(dreport or preport).get("generatedAt") if isinstance((dreport or preport),dict) else None,"missingAfterCount":preport.get("missingAfterCount") if isinstance(preport,dict) else None,"detailErrorCount":(dreport or preport).get("detailErrorCount") if isinstance(preport,dict) else None}
    remaining_private,explicit_aliases,ambiguous_aliases=base.merge_explicit_official_aliases(projected_official,all_private)
    rows,exact_url_groups=dedupe_multi_source(projected_official+remaining_private)
    rows.sort(key=lambda j:(j.get("registered") or "",j.get("applyEnd") or "9999-12-31",j.get("sourceIdentity") or ""),reverse=True)
    per_feed={"official":0,"private":0}; per_private_source_displayed={spec["key"]:0 for spec in PRIVATE_SOURCES}
    for j in rows:
        kind=j.get("feedKind","official"); per_feed[kind]=per_feed.get(kind,0)+1
        if kind=="private":
            src=j.get("source")
            for spec in PRIVATE_SOURCES:
                if src==spec["name"]: per_private_source_displayed[spec["key"]]+=1
    for spec in PRIVATE_SOURCES: private_meta[spec["key"]]["displayedAsPrivate"]=per_private_source_displayed[spec["key"]]
    expected_official_sources=official_source_count(); embedded_official_sources=official_data.get("officialSourceCount") if isinstance(official_data,dict) else None
    if embedded_official_sources is not None and int(embedded_official_sources)!=expected_official_sources:
        raise SystemExit(f"jobs.json officialSourceCount={embedded_official_sources} does not match sources.json={expected_official_sources}")
    foundation_source_ids={str(j.get("foundationRegistryId") or "") for j in foundation_official_jobs if str(j.get("foundationRegistryId") or "")}
    payload={"updatedAt":datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),"dataset":"unified-search-v3-multi-private","officialSourceCount":expected_official_sources,"foundationOfficialSourceCount":len(foundation_source_ids),"totalSourceCount":expected_official_sources+enabled_private_sources,"sources":official_data.get("sources",{}) if isinstance(official_data,dict) else {},"privateSources":private_meta,"counts":{"total":len(rows),**per_feed,"foundationOfficial":len(projected_foundation_official),"privateSourceOccurrences":{spec["key"]:private_meta[spec["key"]]["count"] for spec in PRIVATE_SOURCES}},"jobs":rows}
    Path("unified_jobs.next.json").write_text(json.dumps(payload,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    report={"generatedAt":datetime.now(KST).isoformat(timespec="seconds"),"policy":"unified-search-v3-multi-private-strong-evidence-only","canonicalOfficialJobs":len(official_jobs),"canonicalFoundationOfficialJobs":len(foundation_official_jobs),"canonicalPrivateJobs":canonical_private_total,"selectedCanonicalOfficialJobs":len(projected_canonical_official),"selectedFoundationOfficialJobs":len(projected_foundation_official),"selectedOfficialJobs":len(projected_official),"selectedPrivateJobs":len(all_private),"publishedJobs":len(rows),"perFeed":per_feed,"privateSources":private_meta,"protectedOfficialIds":len(protected),"explicitOfficialAliasGroupsMerged":len(explicit_aliases),"exactUrlAliasGroupsMerged":len(exact_url_groups),"ambiguousExplicitOfficialLinks":len(ambiguous_aliases),"semanticDuplicatePolicy":"same-source/exact-official aliases only; preserve cross-private identities","degradedPrivateSources":degraded_private_sources,"allPublicationEnabledSourcesHealthy":not degraded_private_sources}
    Path("unified_search_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
