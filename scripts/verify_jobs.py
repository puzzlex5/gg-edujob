#!/usr/bin/env python3
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from source_registry import official_source_count

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "jobs.json"
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; metro-edujob-verifier/2.1)"
BAD_PAGE_WORDS = ("페이지를 찾을 수 없습니다", "페이지가 존재하지 않", "사용할 수 없는 페이지", "요청하신 페이지를 찾을 수")
RETRY_STATUSES = (408, 429, 500, 502, 503, 504)


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def central_count(data, province):
    try:
        return int(data["sources"][province]["central"]["count"])
    except Exception:
        return 0


def authoritative_central_count(province):
    """Return the independently reconciled 90-day central population when trustworthy."""
    report = load(ROOT / "source_reconciliation_report.json")
    if not isinstance(report, dict):
        return None
    summary = report.get("summary") or {}
    expected_sources = official_source_count()
    if (
        int(summary.get("totalSources") or 0) != expected_sources
        or int(summary.get("reconciledSources") or 0) != expected_sources
        or int(summary.get("missingAfter") or -1) != 0
        or int(summary.get("lookbackDays") or report.get("lookbackDays") or 0) != 90
    ):
        return None
    expected_name = {
        "gyeonggi": "경기도교육청 통합 구인구직",
        "seoul": "서울교육일자리포털",
    }.get(province)
    if not expected_name:
        return None
    for source in report.get("sources", []):
        if source.get("name") != expected_name:
            continue
        if not source.get("coverageComplete") or not source.get("reconciled"):
            return None
        if int(source.get("missingAfterCount") or 0) != 0:
            return None
        try:
            return int(source.get("officialIdCount"))
        except Exception:
            return None
    return None


def support_issues(data):
    out = []
    for province in ("gyeonggi", "seoul"):
        for s in data.get("sources", {}).get(province, {}).get("supportOffices", []):
            if not s.get("ok"):
                out.append({"name": s.get("name", ""), "state": s.get("state", "error"), "message": s.get("message", "")})
    return out


def exact_support_link_issues(jobs):
    total = 0
    resolved = 0
    bad = []
    for j in jobs:
        if j.get("sourceType") != "교육지원청 개별 게시판":
            continue
        total += 1
        province = j.get("province", "")
        ok = False
        reason = ""
        if province == "경기":
            raw = j.get("url", "") or ""
            try:
                u = urlparse(raw)
                q = parse_qs(u.query)
                sn = (q.get("nttSn") or [str(j.get("nttSn", ""))])[0]
                bbs = (q.get("bbsId") or [str(j.get("bbsId", ""))])[0]
                ok = u.scheme == "https" and u.path.endswith("/selectNttInfo.do") and str(sn).isdigit() and str(bbs).isdigit() and "selectNttList.do" not in raw
                if not ok:
                    reason = "경기 개별공고 URL/nttSn/bbsId 불완전"
            except Exception:
                reason = "경기 개별공고 URL 파싱 실패"
        elif province == "서울":
            seq = str((j.get("openParams") or {}).get("job_seq", ""))
            open_url = j.get("openUrl", "") or ""
            try:
                u = urlparse(open_url)
                ok = j.get("openMethod") == "POST" and seq.isdigit() and u.scheme == "https" and (u.hostname or "").endswith(".sen.go.kr") and u.path == "/FUS/JO/JOV11.do"
                if not ok:
                    reason = "서울 POST 상세열기 정보 불완전"
            except Exception:
                reason = "서울 상세열기 정보 파싱 실패"
        else:
            reason = "지원청 시도 구분 없음"
        if ok:
            resolved += 1
        elif len(bad) < 30:
            bad.append({"source": j.get("source", ""), "title": j.get("title", "")[:100], "reason": reason, "url": j.get("url", "")})
    return {"total": total, "resolved": resolved, "unresolved": total - resolved, "examples": bad}


def normalized_title_tokens(title):
    words = re.findall(r"[0-9A-Za-z가-힣]{2,}", title or "")
    stop = {"채용", "공고", "모집", "학년도", "기간제", "교원", "강사"}
    return [w for w in words if w not in stop][:6]


def build_session():
    session = requests.Session()
    retry = Retry(total=3, connect=3, read=3, status=3, backoff_factor=0.8, status_forcelist=RETRY_STATUSES, allowed_methods=frozenset(["GET", "POST"]), raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry, pool_connections=12, pool_maxsize=12)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    return session


def exact_gyeonggi_identity(request_url, response_url):
    try:
        req = urlparse(request_url)
        res = urlparse(response_url)
        rq = parse_qs(req.query)
        sq = parse_qs(res.query)
        return (
            res.path.endswith("/selectNttInfo.do")
            and (rq.get("nttSn") or [""])[0] == (sq.get("nttSn") or [""])[0]
            and (rq.get("bbsId") or [""])[0] == (sq.get("bbsId") or [""])[0]
        )
    except Exception:
        return False


def verify_links(jobs, limit=50):
    session = build_session()
    checked = 0
    findings = []
    support = [j for j in jobs if j.get("sourceType") == "교육지원청 개별 게시판"]
    central = [j for j in jobs if j.get("sourceType") != "교육지원청 개별 게시판"]
    ordered = []
    picked = set()
    seen_offices = set()

    for j in support:
        office_key = (j.get("province", ""), j.get("source", ""))
        if office_key in seen_offices:
            continue
        seen_offices.add(office_key)
        ordered.append(j)
        picked.add(id(j))

    host_counts = {}
    for j in ordered:
        raw = j.get("openUrl") if j.get("openMethod") == "POST" else j.get("url")
        host = urlparse(raw or "").hostname or ""
        if host:
            host_counts[host] = host_counts.get(host, 0) + 1
    for j in support:
        if len(ordered) >= limit or id(j) in picked:
            continue
        raw = j.get("openUrl") if j.get("openMethod") == "POST" else j.get("url")
        host = urlparse(raw or "").hostname or ""
        if not host or host_counts.get(host, 0) >= 2:
            continue
        ordered.append(j)
        picked.add(id(j))
        host_counts[host] = host_counts.get(host, 0) + 1
    for j in central:
        if len(ordered) >= limit:
            break
        raw = j.get("url") or ""
        host = urlparse(raw).hostname or ""
        if not raw.startswith("http") or not host or host_counts.get(host, 0) >= 2:
            continue
        ordered.append(j)
        host_counts[host] = host_counts.get(host, 0) + 1

    for j in ordered[:limit]:
        raw = j.get("url") or ""
        open_method = j.get("openMethod")
        open_url = j.get("openUrl") or ""
        request_url = open_url if open_method == "POST" and open_url else raw
        host = urlparse(request_url).hostname or ""
        if not request_url.startswith("http") or not host:
            continue
        checked += 1
        base = {"title": j.get("title", "")[:90], "source": j.get("source", ""), "sourceType": j.get("sourceType", ""), "province": j.get("province", ""), "url": request_url}
        try:
            if open_method == "POST" and open_url and (j.get("openParams") or {}).get("job_seq"):
                r = session.post(open_url, data={"job_seq": str(j["openParams"]["job_seq"])}, timeout=(6, 15), allow_redirects=True)
            else:
                r = session.get(raw, timeout=(6, 15), allow_redirects=True)
            content_type = (r.headers.get("content-type") or "").lower()
            text = r.text[:180000] if "text" in content_type or "html" in content_type or not content_type else ""
            hard = r.status_code >= 400 or any(x in text for x in BAD_PAGE_WORDS)
            reason = "http-or-error-page" if hard else ""
            soft = False

            if not hard and j.get("sourceType") == "교육지원청 개별 게시판":
                if j.get("province") == "경기":
                    if "selectNttList.do" in r.url or not exact_gyeonggi_identity(raw, r.url):
                        hard = True
                        reason = "gyeonggi-detail-redirect-or-id-mismatch"
                    else:
                        tokens = normalized_title_tokens(j.get("title", ""))
                        if tokens and text and sum(1 for t in tokens if t in text) == 0:
                            soft = True
                            reason = "title-evidence-missing"
                elif j.get("province") == "서울":
                    if re.search(r"Total\s*:\s*\d+\s*개", text) and "상세" not in text:
                        hard = True
                        reason = "seoul-list-page-returned"
                    else:
                        tokens = normalized_title_tokens(j.get("title", ""))
                        if tokens and text and sum(1 for t in tokens if t in text) == 0:
                            soft = True
                            reason = "title-evidence-missing"

            if hard or soft:
                findings.append({**base, "status": r.status_code, "hard": bool(hard), "reason": reason, "finalUrl": r.url})
        except Exception as e:
            findings.append({**base, "status": type(e).__name__, "hard": False, "reason": "transient-network-after-retries"})
    return checked, findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--previous", default="")
    args = ap.parse_args()
    data = load(OUT)
    if not data:
        print("jobs.json unreadable", file=sys.stderr)
        return 2
    jobs = data.get("jobs", [])
    prev = load(args.previous) if args.previous else None
    warnings, critical = [], []
    total = len(jobs)
    gg, se = central_count(data, "gyeonggi"), central_count(data, "seoul")
    if total < 100:
        critical.append(f"전체 공고가 비정상적으로 적음: {total}")
    if gg < 20:
        critical.append(f"경기도교육청 중앙 수집 급감: {gg}")
    if se < 20:
        critical.append(f"서울 중앙 수집 급감: {se}")
    if prev:
        prev_total = len(prev.get("jobs", []))
        if prev_total >= 100 and total < prev_total * 0.35:
            critical.append(f"이전 {prev_total}건에서 {total}건으로 65% 이상 급감")
        for key, label in (("gyeonggi", "경기"), ("seoul", "서울")):
            before, after = central_count(prev, key), central_count(data, key)
            if before >= 50 and after < before * 0.35:
                authoritative = authoritative_central_count(key)
                tolerance = max(5, int(authoritative * 0.02)) if authoritative is not None else 0
                verified_retention_cleanup = (
                    authoritative is not None
                    and abs(after - authoritative) <= tolerance
                    and before > authoritative * 1.5
                )
                if verified_retention_cleanup:
                    warnings.append(
                        f"{label} 중앙 이전 기준 과수집 정상화: {before}→{after} "
                        f"(독립 90일 공식 ID {authoritative})"
                    )
                else:
                    critical.append(f"{label} 중앙 공고 급감: {before}→{after}")
    issues = support_issues(data)
    if issues:
        warnings.append(f"교육지원청 확인 필요 {len(issues)}곳")
    exact = exact_support_link_issues(jobs)
    if exact["unresolved"]:
        critical.append(f"교육지원청 개별공고 링크 미해결: {exact['unresolved']}/{exact['total']}")

    checked, findings = verify_links(jobs)
    hard_failed = [f for f in findings if f.get("hard")]
    soft_findings = [f for f in findings if not f.get("hard")]
    support_failed = [f for f in hard_failed if f.get("sourceType") == "교육지원청 개별 게시판"]
    if support_failed:
        offices = sorted({f.get("source", "") for f in support_failed if f.get("source")})
        label = ", ".join(offices[:5]) + (f" 외 {len(offices)-5}곳" if len(offices) > 5 else "")
        critical.append(f"교육지원청 실제 상세페이지 하드 실패: {len(support_failed)}건" + (f" ({label})" if label else ""))
    if checked and len(hard_failed) / checked > 0.35:
        critical.append(f"원문 링크 하드 실패율 과다: {len(hard_failed)}/{checked}")
    if soft_findings:
        warnings.append(f"원문 링크 일시/본문증거 확인 필요 {len(soft_findings)}/{checked}개")
    if hard_failed and not support_failed:
        warnings.append(f"중앙 원문 링크 하드 실패 {len(hard_failed)}/{checked}개")

    state = "critical" if critical else ("warning" if warnings else "ok")
    data["verification"] = {
        "state": state,
        "checkedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "message": " · ".join(critical + warnings) if (critical or warnings) else "2차 검증 정상",
        "supportOfficeIssues": issues,
        "supportExactLinks": exact,
        "linkChecks": {
            "checked": checked,
            "failed": len(hard_failed),
            "soft": len(soft_findings),
            "supportFailed": len(support_failed),
            "failures": (hard_failed + soft_findings)[:12],
        },
        "previousTotal": len(prev.get("jobs", [])) if prev else None,
        "currentTotal": total,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data["verification"], ensure_ascii=False))
    return 2 if critical else 0


if __name__ == "__main__":
    raise SystemExit(main())
