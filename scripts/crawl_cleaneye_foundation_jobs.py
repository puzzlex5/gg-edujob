#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
BASE = "https://job.cleaneye.go.kr"
PAGE = BASE + "/user/ypRecruitment.do"
API = BASE + "/user/selectYpRecruitment.do"
DETAIL = BASE + "/user/ypCareersData.do"
REGISTRY = Path("cultural_foundation_registry.json")
OUT = Path("cleaneye_foundation_jobs.json")
REPORT = Path("cleaneye_foundation_report.json")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36"
CLOSED_STATUS = {"709003"}


def norm(s: str) -> str:
    s = re.sub(r"\(\s*재\s*\)|재단법인", "", str(s or ""), flags=re.I)
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", s).lower()


def parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(str(raw or "")[:10])
    except Exception:
        return None


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "ko-KR,ko;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": PAGE,
    })
    s.get(PAGE, timeout=30).raise_for_status()
    return s


def exact_ent_match(found: str, foundation: dict) -> bool:
    target = {norm(foundation.get("name")), *(norm(x) for x in foundation.get("aliases") or [])}
    target.discard("")
    return norm(found) in target


def external_detail_links(s: requests.Session, detail_url: str) -> list[dict]:
    r = s.get(detail_url, timeout=30, headers={"X-Requested-With": ""})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(detail_url, a.get("href"))
        p = urlparse(href)
        if p.scheme not in {"http", "https"}:
            continue
        host = (p.hostname or "").lower()
        if host.endswith("cleaneye.go.kr"):
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append({"text": " ".join(a.get_text(" ", strip=True).split())[:200], "url": href})
    return out[:30]


def collect_foundation(s: requests.Session, foundation: dict) -> tuple[list[dict], dict]:
    name = str(foundation["name"])
    page_no = 1
    rows = []
    total = None
    while True:
        payload = {
            "pageIndex": str(page_no),
            "pageUnit": "10",
            "pageSize": "10",
            "status": "",
            "entName": name,
            "searchKeyword": name,
        }
        r = s.post(API, data=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        if total is None:
            total = int(data.get("cnt") or 0)
        batch = data.get("list") or []
        if not batch:
            break
        rows.extend(batch)
        if page_no >= max(1, math.ceil(total / 10)):
            break
        page_no += 1
        if page_no > 100:
            raise RuntimeError(f"CleanEye pagination safety cap: {name} total={total}")
    exact = [r for r in rows if exact_ent_match(r.get("entName", ""), foundation)]
    return exact, {"query": name, "apiRows": len(rows), "exactRows": len(exact), "pages": page_no, "reportedCount": total}


def main() -> int:
    now = datetime.now(KST)
    today = now.date()
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    foundations = [x for x in registry.get("institutions", []) if x.get("enabled") is not False]
    s = session()
    jobs = []
    institution_reports = []
    errors = []
    for foundation in foundations:
        try:
            rows, meta = collect_foundation(s, foundation)
            meta.update({"foundationRegistryId": foundation["id"], "foundationName": foundation["name"]})
            current = []
            for row in rows:
                status = str(row.get("status") or "")
                end = parse_date(row.get("pubEndDate"))
                if status in CLOSED_STATUS:
                    continue
                if end and end < today:
                    continue
                empyear = str(row.get("empyear") or "")
                ent_id = str(row.get("ypEntId") or "")
                seq = str(row.get("entSeq") or "")
                if not (empyear and ent_id and seq):
                    continue
                params = {"empyear": empyear, "entSeq": seq, "ypEntId": ent_id}
                detail_url = DETAIL + "?" + urlencode(params)
                links = []
                try:
                    links = external_detail_links(s, detail_url)
                except Exception as exc:
                    meta.setdefault("detailWarnings", []).append({"id": f"{empyear}:{ent_id}:{seq}", "error": f"{type(exc).__name__}: {exc}"})
                job = {
                    "sourceIdentity": f"cleaneye:{empyear}:{ent_id}:{seq}",
                    "source": "클린아이 잡플러스",
                    "sourceRole": "public-foundation-cross-check",
                    "foundationRegistryId": foundation["id"],
                    "foundationName": foundation["name"],
                    "region": foundation["region"],
                    "municipality": foundation["municipality"],
                    "organization": row.get("entName"),
                    "title": row.get("entTitle"),
                    "registered": row.get("pubDate") or "",
                    "applyEnd": row.get("pubEndDate") or "",
                    "statusCode": status,
                    "cleaneyeUrl": detail_url,
                    "officialLinkCandidates": links,
                    "url": "",
                    "originalUrl": "",
                }
                jobs.append(job)
                current.append(job["sourceIdentity"])
            meta["currentRows"] = len(current)
            institution_reports.append(meta)
        except Exception as exc:
            errors.append({"foundationRegistryId": foundation["id"], "foundationName": foundation["name"], "error": f"{type(exc).__name__}: {exc}"})

    by_id = {j["sourceIdentity"]: j for j in jobs}
    jobs = list(by_id.values())
    seen_institutions = sorted({j["foundationRegistryId"] for j in jobs})
    generated = now.isoformat(timespec="seconds")
    OUT.write_text(json.dumps({"generatedAt": generated, "source": "클린아이 잡플러스", "jobs": jobs}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    report = {
        "generatedAt": generated,
        "source": "클린아이 잡플러스",
        "role": "public-foundation-cross-check",
        "healthy": not errors and len(institution_reports) == len(foundations),
        "registryInstitutions": len(foundations),
        "institutionsQueried": len(institution_reports),
        "currentJobs": len(jobs),
        "institutionsWithCurrentJobs": len(seen_institutions),
        "institutionIdsWithCurrentJobs": seen_institutions,
        "institutions": institution_reports,
        "errors": errors,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("healthy", "registryInstitutions", "institutionsQueried", "currentJobs", "institutionsWithCurrentJobs", "errors")}, ensure_ascii=False, indent=2))
    return 0 if report["healthy"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
