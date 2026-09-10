#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
REGISTRY = Path("cultural_foundation_registry.json")
OVERRIDES = Path("verified_foundation_official_sources.json")
OUTPUT = Path("official_foundation_jobs.json")
REPORT = Path("official_foundation_report.json")

UA = "gg-edujob/official-foundation-crawler (+https://github.com/teacherhub-kr/gg-edujob)"
RESULT_RE = re.compile(r"최종\s*합격|합격자|서류\s*(?:심사|전형)|면접\s*(?:심사|전형|대상)|선정\s*결과|채용\s*결과|전형\s*결과|인사\s*발령")
DATE_RE = re.compile(r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})")
KOREAN_DATE_RE = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
MONTH_DAY_RE = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
PERIOD_HINT_RE = re.compile(r"접수\s*기간|원서\s*접수|모집\s*기간|공고\s*기간|응시원서", re.I)


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_date_text(value: str) -> date | None:
    text = str(value or "")
    for rx in (DATE_RE, KOREAN_DATE_RE):
        m = rx.search(text)
        if not m:
            continue
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def parse_all_dates(value: str) -> list[date]:
    out: list[date] = []
    for rx in (DATE_RE, KOREAN_DATE_RE):
        for m in rx.finditer(str(value or "")):
            try:
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            if d not in out:
                out.append(d)
    return out


def format_date(value: date | None) -> str:
    return value.isoformat() if value else ""


def effective_foundations() -> list[dict]:
    registry = load(REGISTRY, {})
    foundations = [dict(x) for x in registry.get("institutions", []) if x.get("enabled") is not False]
    by_id = {str(x.get("id") or ""): x for x in foundations}
    overrides = load(OVERRIDES, {})
    for item in overrides.get("sources", []) if isinstance(overrides, dict) else []:
        fid = str(item.get("foundationRegistryId") or "")
        target = by_id.get(fid)
        if not target:
            continue
        for key in ("homepage", "officialRecruitmentUrl"):
            value = str(item.get(key) or "").strip()
            if value:
                target[key] = value
        target["officialSourceVerifiedAt"] = str(item.get("verifiedAt") or "")
    return foundations


def request(session: requests.Session, url: str) -> requests.Response:
    r = session.get(
        url,
        timeout=25,
        headers={
            "User-Agent": UA,
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        },
        allow_redirects=True,
    )
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r


def candidate_period_segments(text: str) -> list[str]:
    segments = []
    for m in PERIOD_HINT_RE.finditer(text):
        segments.append(text[m.start():m.start() + 500])
    return segments or [text[:1600]]


def extract_apply_end(text: str, registered: date | None) -> date | None:
    candidates: list[date] = []
    base_year = registered.year if registered else datetime.now(KST).year
    for segment in candidate_period_segments(normalize_space(text)):
        for rx in (DATE_RE, KOREAN_DATE_RE):
            for m in rx.finditer(segment):
                try:
                    candidates.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
                except ValueError:
                    pass
        for m in MONTH_DAY_RE.finditer(segment):
            try:
                candidates.append(date(base_year, int(m.group(1)), int(m.group(2))))
            except ValueError:
                pass
        m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일?\s*부터\s*(\d{1,2})\s*일?\s*까지", segment)
        if m:
            try:
                candidates.append(date(base_year, int(m.group(1)), int(m.group(3))))
            except ValueError:
                pass
        m = re.search(r"(20\d{2})\s*[.]\s*(\d{1,2})\s*[.]\s*(\d{1,2}).{0,40}?[~～-].{0,20}?(\d{1,2})\s*[.]\s*(\d{1,2})", segment)
        if m:
            try:
                candidates.append(date(int(m.group(1)), int(m.group(4)), int(m.group(5))))
            except ValueError:
                pass

    if not candidates:
        return None
    lower = (registered or datetime.now(KST).date()) - timedelta(days=2)
    upper = (registered or datetime.now(KST).date()) + timedelta(days=150)
    plausible = [d for d in candidates if lower <= d <= upper]
    return max(plausible) if plausible else None


def detail_title(soup: BeautifulSoup, fallback: str = "") -> str:
    # Prefer the page's visible article heading, but avoid relying on one markup version.
    for selector in ("h3", "h4", ".board-view-title", ".view-title", ".title"):
        for node in soup.select(selector):
            text = normalize_space(node.get_text(" ", strip=True))
            if text and ("공고" in text or "모집" in text or "채용" in text) and len(text) >= 8:
                return text
    return normalize_space(fallback)


def detail_registered(soup: BeautifulSoup, fallback: date | None) -> date | None:
    text = normalize_space(soup.get_text(" ", strip=True))
    today = datetime.now(KST).date()
    dates = [d for d in parse_all_dates(text[:2600]) if d <= today]
    # The page metadata date is normally the newest past date near the heading. A list-row
    # date, when present, is stronger and avoids confusing dates embedded in the notice body.
    if fallback:
        return fallback
    recent = [d for d in dates if d >= today - timedelta(days=120)]
    return min(recent, key=lambda d: abs((today - d).days)) if recent else None


def nsart_detail_candidates(session: requests.Session, foundation: dict, board_url: str) -> tuple[dict[str, dict], list[str]]:
    """Collect detail IDs from two independent surfaces.

    nsart's recruitment list can be served from a stale cache to some clients. The homepage has a
    separate current-recruitment surface, so use both. Missing a link on either surface must not hide
    a current official posting.
    """
    surfaces = [board_url]
    homepage = str(foundation.get("homepage") or "").strip()
    if homepage:
        surfaces.append(homepage)
    candidates: dict[str, dict] = {}
    surface_urls: list[str] = []
    for surface in surfaces:
        r = request(session, surface)
        surface_urls.append(r.url)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = str(a.get("href") or "")
            absolute = urljoin(r.url, href)
            parsed = urlparse(absolute)
            q = parse_qs(parsed.query)
            bpo = str((q.get("bpoId") or [""])[0])
            if not bpo.isdigit() or not parsed.path.endswith("/board/recruit.do") or "act=read" not in parsed.query:
                continue
            row_text = normalize_space(a.parent.parent.get_text(" ", strip=True)) if a.parent and a.parent.parent else ""
            reg = parse_date_text(row_text)
            existing = candidates.setdefault(bpo, {"url": absolute, "fallbackTitle": "", "registered": None})
            title = normalize_space(a.get_text(" ", strip=True))
            if title and len(title) > len(existing.get("fallbackTitle") or ""):
                existing["fallbackTitle"] = title
            if reg:
                existing["registered"] = reg
    return candidates, surface_urls


def nsart_rows(session: requests.Session, foundation: dict, board_url: str) -> tuple[list[dict], dict]:
    today = datetime.now(KST).date()
    rows = []
    candidates, surfaces = nsart_detail_candidates(session, foundation, board_url)
    inspected = 0

    for bpo, meta in sorted(candidates.items(), key=lambda item: int(item[0]), reverse=True):
        inspected += 1
        detail = request(session, str(meta["url"]))
        detail_soup = BeautifulSoup(detail.text, "html.parser")
        title = detail_title(detail_soup, str(meta.get("fallbackTitle") or ""))
        reg = detail_registered(detail_soup, meta.get("registered"))
        if not title or not reg or reg < today - timedelta(days=90) or reg > today:
            continue
        if RESULT_RE.search(title):
            continue
        detail_text = detail_soup.get_text(" ", strip=True)
        end = extract_apply_end(detail_text, reg)
        if end and end < today:
            continue

        fid = str(foundation.get("id") or "")
        rows.append({
            "sourceIdentity": f"official-foundation:nsart:{bpo}",
            "foundationRegistryId": fid,
            "foundationName": foundation.get("name") or "광주시문화재단",
            "organization": foundation.get("name") or "광주시문화재단",
            "source": foundation.get("name") or "광주시문화재단",
            "sourceType": "문화재단 공식채용",
            "sourceSurface": "cultural-foundation",
            "sourceSurfaceLabel": f"{foundation.get('name') or '문화재단'} 공식 채용공고",
            "sourceRole": "primary-official",
            "trustLevel": "공식",
            "province": foundation.get("region") or "경기",
            "region": foundation.get("municipality") or "",
            "regions": [foundation.get("municipality")] if foundation.get("municipality") else [],
            "location": " ".join(x for x in [foundation.get("region"), foundation.get("municipality")] if x),
            "title": title,
            "registered": format_date(reg),
            "applyEnd": format_date(end),
            "url": detail.url,
            "originalUrl": detail.url,
            "detailUrl": detail.url,
            "boardUrl": board_url,
            "detailLinkVerified": True,
            "detailLinkReason": "official-foundation-detail-id",
            "transportVerified": True,
        })

    return rows, {
        "adapter": "nsart",
        "surfacesChecked": surfaces,
        "discoveredDetailLinks": len(candidates),
        "inspectedDetailLinks": inspected,
        "publishedCurrentJobs": len(rows),
    }


def main() -> int:
    generated = datetime.now(KST).isoformat(timespec="seconds")
    foundations = effective_foundations()
    configured = [x for x in foundations if str(x.get("officialRecruitmentUrl") or "").strip()]
    session = requests.Session()
    jobs: list[dict] = []
    errors = []
    unsupported = []
    board_results = []

    for foundation in configured:
        board_url = str(foundation.get("officialRecruitmentUrl") or "").strip()
        host = (urlparse(board_url).hostname or "").lower()
        try:
            if host == "nsart.or.kr" or host.endswith(".nsart.or.kr"):
                found, meta = nsart_rows(session, foundation, board_url)
                jobs.extend(found)
                board_results.append({
                    "foundationRegistryId": foundation.get("id"),
                    "foundationName": foundation.get("name"),
                    "boardUrl": board_url,
                    "healthy": True,
                    **meta,
                })
            else:
                unsupported.append({
                    "foundationRegistryId": foundation.get("id"),
                    "foundationName": foundation.get("name"),
                    "boardUrl": board_url,
                    "reason": "adapter-not-yet-implemented",
                })
        except Exception as exc:
            errors.append({
                "foundationRegistryId": foundation.get("id"),
                "foundationName": foundation.get("name"),
                "boardUrl": board_url,
                "error": f"{type(exc).__name__}: {exc}",
            })
            board_results.append({
                "foundationRegistryId": foundation.get("id"),
                "foundationName": foundation.get("name"),
                "boardUrl": board_url,
                "healthy": False,
            })

    ids = [str(x.get("sourceIdentity") or "") for x in jobs]
    urls = [str(x.get("url") or "") for x in jobs]
    duplicate_ids = sorted({x for x in ids if x and ids.count(x) > 1})
    duplicate_urls = sorted({x for x in urls if x and urls.count(x) > 1})
    if duplicate_ids or duplicate_urls:
        errors.append({"error": "duplicate-official-identities", "ids": duplicate_ids, "urls": duplicate_urls})

    jobs.sort(key=lambda x: (str(x.get("registered") or ""), str(x.get("sourceIdentity") or "")), reverse=True)
    healthy = not errors
    payload = {"generatedAt": generated, "sourceRole": "primary-official", "jobs": jobs}
    report = {
        "generatedAt": generated,
        "policy": "official-foundation-primary-fail-closed-v2",
        "healthy": healthy,
        "registryInstitutions": len(foundations),
        "officialBoardsConfigured": len(configured),
        "supportedBoardsChecked": len(board_results),
        "unsupportedConfiguredBoards": unsupported,
        "jobs": len(jobs),
        "errors": errors,
        "boards": board_results,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
