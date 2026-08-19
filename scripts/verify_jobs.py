#!/usr/bin/env python3
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "jobs.json"
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; metro-edujob-verifier/2.0)"
BAD_PAGE_WORDS = ("페이지를 찾을 수 없습니다", "페이지가 존재하지 않", "사용할 수 없는 페이지", "요청하신 페이지를 찾을 수")


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


def support_issues(data):
    out = []
    for province in ("gyeonggi", "seoul"):
        for s in data.get("sources", {}).get(province, {}).get("supportOffices", []):
            if not s.get("ok"):
                out.append({"name": s.get("name", ""), "state": s.get("state", "error"), "message": s.get("message", "")})
    return out


def exact_support_link_issues(jobs):
    """Enforce the product invariant: a support-office card must open its exact posting."""
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
                ok = (
                    u.scheme == "https"
                    and u.path.endswith("/selectNttInfo.do")
                    and str(sn).isdigit()
                    and str(bbs).isdigit()
                    and "selectNttList.do" not in raw
                )
                if not ok:
                    reason = "경기 개별공고 URL/nttSn/bbsId 불완전"
            except Exception:
                reason = "경기 개별공고 URL 파싱 실패"
        elif province == "서울":
            seq = str((j.get("openParams") or {}).get("job_seq", ""))
            open_url = j.get("openUrl", "") or ""
            try:
                u = urlparse(open_url)
                ok = (
                    j.get("openMethod") == "POST"
                    and seq.isdigit()
                    and u.scheme == "https"
                    and (u.hostname or "").endswith(".sen.go.kr")
                    and u.path == "/FUS/JO/JOV11.do"
                )
                if not ok:
                    reason = "서울 POST 상세열기 정보 불완전"
            except Exception:
                reason = "서울 상세열기 정보 파싱 실패"
        else:
            reason = "지원청 시도 구분 없음"

        if ok:
            resolved += 1
        elif len(bad) < 30:
            bad.append({
                "source": j.get("source", ""),
                "title": j.get("title", "")[:100],
                "reason": reason,
                "url": j.get("url", ""),
            })
    return {"total": total, "resolved": resolved, "unresolved": total - resolved, "examples": bad}


def normalized_title_tokens(title):
    # Avoid false mismatches from punctuation/spacing while still checking that the detail page is the same posting.
    words = re.findall(r"[0-9A-Za-z가-힣]{2,}", title or "")
    stop = {"채용", "공고", "모집", "학년도", "기간제", "교원", "강사"}
    return [w for w in words if w not in stop][:6]


def verify_links(jobs, limit=50):
    """Open real detail pages, covering every support office that currently has a collected job.

    The old verifier simply walked jobs in file order and allowed five checks per host. That
    could spend all 36 checks on the first few offices and still report a reassuring aggregate
    failure rate. Build an office-stratified sample first so one broken office cannot hide behind
    healthy neighbors, then use any remaining budget for extra support/central samples.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    checked = 0
    failed = []

    support = [j for j in jobs if j.get("sourceType") == "교육지원청 개별 게시판"]
    central = [j for j in jobs if j.get("sourceType") != "교육지원청 개별 게시판"]
    ordered = []
    picked = set()
    seen_offices = set()

    # First pass: exactly one representative posting per support office/province.
    for j in support:
        office_key = (j.get("province", ""), j.get("source", ""))
        if office_key in seen_offices:
            continue
        seen_offices.add(office_key)
        ordered.append(j)
        picked.add(id(j))

    # Second pass: add extra support-office samples, capped at two per host.
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

    # Use any remaining budget for central-board links without reducing support-office coverage.
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
        if open_method == "POST" and open_url:
            request_url = open_url
            host = urlparse(open_url).hostname or ""
        else:
            request_url = raw
            host = urlparse(raw).hostname or ""
        if not request_url.startswith("http") or not host:
            continue
        checked += 1
        try:
            if open_method == "POST" and open_url and (j.get("openParams") or {}).get("job_seq"):
                r = session.post(open_url, data={"job_seq": str(j["openParams"]["job_seq"])}, timeout=13, allow_redirects=True)
            else:
                r = session.get(raw, timeout=13, allow_redirects=True)
            content_type = r.headers.get("content-type") or ""
            text = r.text[:180000] if "text" in content_type or "html" in content_type else ""
            bad = r.status_code >= 400 or any(x in text for x in BAD_PAGE_WORDS)

            # A 200 response is not enough. For support-office postings, require evidence that the returned page is detail content.
            if not bad and j.get("sourceType") == "교육지원청 개별 게시판":
                if j.get("province") == "경기" and "selectNttList.do" in r.url:
                    bad = True
                tokens = normalized_title_tokens(j.get("title", ""))
                if tokens and text:
                    hits = sum(1 for t in tokens if t in text)
                    if hits == 0:
                        bad = True
                if j.get("province") == "서울" and re.search(r"Total\s*:\s*\d+\s*개", text) and "상세" not in text:
                    bad = True

            if bad:
                failed.append({"title": j.get("title", "")[:90], "source": j.get("source", ""), "url": request_url, "status": r.status_code})
        except Exception as e:
            failed.append({"title": j.get("title", "")[:90], "source": j.get("source", ""), "url": request_url, "status": type(e).__name__})
    return checked, failed


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
    warnings = []
    critical = []

    total = len(jobs)
    gg = central_count(data, "gyeonggi")
    se = central_count(data, "seoul")
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
            before = central_count(prev, key)
            after = central_count(data, key)
            if before >= 50 and after < before * 0.35:
                critical.append(f"{label} 중앙 공고 급감: {before}→{after}")

    issues = support_issues(data)
    if issues:
        warnings.append(f"교육지원청 확인 필요 {len(issues)}곳")

    exact = exact_support_link_issues(jobs)
    if exact["unresolved"]:
        # Wrong-board navigation is worse than temporarily preserving the prior known-good dataset.
        critical.append(f"교육지원청 개별공고 링크 미해결: {exact['unresolved']}/{exact['total']}")

    checked, failed = verify_links(jobs)
    if checked and len(failed) / checked > 0.35:
        critical.append(f"원문 링크 검사 실패율 과다: {len(failed)}/{checked}")
    elif failed:
        warnings.append(f"원문 링크 {len(failed)}/{checked}개 확인 필요")

    state = "critical" if critical else ("warning" if warnings else "ok")
    data["verification"] = {
        "state": state,
        "checkedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "message": " · ".join(critical + warnings) if (critical or warnings) else "2차 검증 정상",
        "supportOfficeIssues": issues,
        "supportExactLinks": exact,
        "linkChecks": {"checked": checked, "failed": len(failed), "failures": failed[:12]},
        "previousTotal": len(prev.get("jobs", [])) if prev else None,
        "currentTotal": total,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data["verification"], ensure_ascii=False))
    return 2 if critical else 0


if __name__ == "__main__":
    raise SystemExit(main())
