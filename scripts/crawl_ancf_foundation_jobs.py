#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
KST = timezone(timedelta(hours=9))
BASE = "https://www.ancf.or.kr"
LIST = BASE + "/list/%EC%82%AC%EB%9E%8C?page={page}"
OUT = Path("ancf_foundation_jobs.json")
REPORT = Path("ancf_foundation_report.json")
REGISTRY = Path("cultural_foundation_registry.json")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36"
DATE_RANGE_RE = re.compile(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})\s*[-~]\s*(20\d{2})?[.\-/]?(\d{1,2})[.\-/](\d{1,2})")
POST_RE = re.compile(r"^/p/([0-9a-f]+)$", re.I)


def norm(s: str) -> str:
    s = re.sub(r"\(\s*재\s*\)|\[\s*재\s*\]|재단법인", "", str(s or ""), flags=re.I)
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", s).lower()


def load_registry():
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    rows = [r for r in data.get("institutions", []) if r.get("enabled") is not False]
    alias_map = []
    for row in rows:
        aliases = [row.get("name"), *(row.get("aliases") or [])]
        for alias in aliases:
            n = norm(alias)
            if n:
                alias_map.append((n, row))
    alias_map.sort(key=lambda x: len(x[0]), reverse=True)
    return rows, alias_map


def match_institution(text: str, alias_map):
    n = norm(text)
    for alias, row in alias_map:
        if alias and alias in n:
            return row
    return None


def iso(y: int, m: int, d: int) -> str:
    try:
        return date(y, m, d).isoformat()
    except Exception:
        return ""


def parse_range(text: str):
    m = DATE_RANGE_RE.search(text)
    if not m:
        return "", ""
    y1, m1, d1, y2, m2, d2 = m.groups()
    y1i = int(y1)
    y2i = int(y2) if y2 else y1i
    return iso(y1i, int(m1), int(d1)), iso(y2i, int(m2), int(d2))


def session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    return s


def fetch(s: requests.Session, url: str) -> BeautifulSoup:
    # ANCF currently serves a certificate chain that GitHub runners cannot validate.
    # This feed is discovery/audit-only; no URL from it is published to users without
    # an independent official-source verification.
    r = s.get(url, timeout=30, verify=False)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def collect(max_pages: int = 40, horizon_days: int = 240):
    _, alias_map = load_registry()
    s = session()
    today = datetime.now(KST).date()
    cutoff = today - timedelta(days=horizon_days)
    seen = set()
    jobs = []
    pages = []
    consecutive_old_pages = 0

    for page_no in range(max_pages):
        url = LIST.format(page=page_no)
        soup = fetch(s, url)
        page_posts = []
        for a in soup.find_all("a", href=True):
            path = urlparse(urljoin(BASE, a.get("href"))).path
            m = POST_RE.match(path)
            if not m:
                continue
            post_id = m.group(1)
            if post_id in seen:
                continue
            container = a
            for _ in range(4):
                if container.parent is None:
                    break
                container = container.parent
                txt = " ".join(container.get_text(" ", strip=True).split())
                if len(txt) > 30:
                    break
            text = " ".join(container.get_text(" ", strip=True).split())
            title = " ".join(a.get_text(" ", strip=True).split()) or text
            inst = match_institution(title + " " + text, alias_map)
            if not inst:
                continue
            start, end = parse_range(text)
            page_posts.append({
                "postId": post_id,
                "institution": inst,
                "title": title,
                "listText": text,
                "registered": start,
                "applyEnd": end,
                "url": urljoin(BASE, a.get("href")),
            })
            seen.add(post_id)

        pages.append({"page": page_no, "matched": len(page_posts)})
        if not page_posts and page_no >= 2:
            consecutive_old_pages += 1
        else:
            ends = [date.fromisoformat(x["applyEnd"]) for x in page_posts if x.get("applyEnd")]
            if ends and max(ends) < cutoff:
                consecutive_old_pages += 1
            else:
                consecutive_old_pages = 0

        for item in page_posts:
            inst = item.pop("institution")
            jobs.append({
                "sourceIdentity": f"ancf:{item['postId']}",
                "source": "전국지역문화재단연합회",
                "sourceRole": "cross-check-only",
                "sourceSurface": "people",
                "foundationRegistryId": inst["id"],
                "foundationName": inst["name"],
                "region": inst["region"],
                "municipality": inst["municipality"],
                "title": item["title"],
                "registered": item["registered"],
                "applyEnd": item["applyEnd"],
                "auditUrl": item["url"],
                "url": "",
                "originalUrl": "",
                "transportVerified": False,
                "rawListText": item["listText"],
            })
        if consecutive_old_pages >= 2:
            break
    return jobs, pages


def main() -> int:
    generated = datetime.now(KST).isoformat(timespec="seconds")
    errors = []
    try:
        jobs, pages = collect()
    except Exception as exc:
        jobs, pages = [], []
        errors.append(f"{type(exc).__name__}: {exc}")
    institutions = sorted({j["foundationRegistryId"] for j in jobs})
    healthy = not errors and bool(pages)
    OUT.write_text(json.dumps({"generatedAt": generated, "source": "전국지역문화재단연합회", "jobs": jobs}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    report = {
        "generatedAt": generated,
        "source": "전국지역문화재단연합회",
        "role": "cross-check-only",
        "healthy": healthy,
        "transportVerified": False,
        "jobs": len(jobs),
        "institutionsSeen": len(institutions),
        "institutionIds": institutions,
        "pages": pages,
        "errors": errors,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
