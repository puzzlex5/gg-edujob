#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "jobs.json"
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; metro-edujob-verifier/1.0)"
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


def verify_links(jobs, limit=24):
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    checked = 0
    failed = []
    seen_hosts = {}
    # 한 사이트에 요청이 몰리지 않도록 출처별 최대 5개만 검사
    for j in jobs:
        url = j.get("url") or ""
        if not url.startswith("http"): continue
        host = url.split("/", 3)[2]
        if seen_hosts.get(host, 0) >= 5: continue
        seen_hosts[host] = seen_hosts.get(host, 0) + 1
        checked += 1
        try:
            r = session.get(url, timeout=13, allow_redirects=True)
            text = r.text[:120000] if "text" in (r.headers.get("content-type") or "") or "html" in (r.headers.get("content-type") or "") else ""
            bad = r.status_code >= 400 or any(x in text for x in BAD_PAGE_WORDS)
            if bad:
                failed.append({"title": j.get("title", "")[:90], "source": j.get("source", ""), "url": url, "status": r.status_code})
        except Exception as e:
            failed.append({"title": j.get("title", "")[:90], "source": j.get("source", ""), "url": url, "status": type(e).__name__})
        if checked >= limit: break
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
    if total < 100: critical.append(f"전체 공고가 비정상적으로 적음: {total}")
    if gg < 20: critical.append(f"경기도교육청 중앙 수집 급감: {gg}")
    if se < 20: critical.append(f"서울 중앙 수집 급감: {se}")

    if prev:
        prev_total = len(prev.get("jobs", []))
        if prev_total >= 100 and total < prev_total * 0.35:
            critical.append(f"이전 {prev_total}건에서 {total}건으로 65% 이상 급감")
        for key, label in (("gyeonggi", "경기"), ("seoul", "서울")):
            before = central_count(prev, key); after = central_count(data, key)
            if before >= 50 and after < before * 0.35:
                critical.append(f"{label} 중앙 공고 급감: {before}→{after}")

    issues = support_issues(data)
    if issues:
        warnings.append(f"교육지원청 확인 필요 {len(issues)}곳")

    checked, failed = verify_links(jobs)
    if checked and len(failed) / checked > 0.5:
        critical.append(f"원문 링크 검사 실패율 과다: {len(failed)}/{checked}")
    elif failed:
        warnings.append(f"원문 링크 {len(failed)}/{checked}개 확인 필요")

    state = "critical" if critical else ("warning" if warnings else "ok")
    data["verification"] = {
        "state": state,
        "checkedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "message": " · ".join(critical + warnings) if (critical or warnings) else "2차 검증 정상",
        "supportOfficeIssues": issues,
        "linkChecks": {"checked": checked, "failed": len(failed), "failures": failed[:10]},
        "previousTotal": len(prev.get("jobs", [])) if prev else None,
        "currentTotal": total
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data["verification"], ensure_ascii=False))
    return 2 if critical else 0


if __name__ == "__main__":
    raise SystemExit(main())
