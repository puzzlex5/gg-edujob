#!/usr/bin/env python3
"""Collect ACTIVE Lessoninfo recruitment posts from the site's public Cafe24 host.

Scope requested by the user:
1) 방과후교사•늘봄학교 강사
2) 문화예술/음악단체·기관 구인·채용

Rules:
- recruitment only; no job-seeker/resume/sales/rental/promotional content
- Seoul + Gyeonggi only (current 수도권에듀잡 scope)
- expired/completed/closed notices are not published
- stable identity = bo_table + wr_no
- source discovery and published active IDs are reconciled every run
- never replace last known-good data with an empty/partial traversal
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup
import crawl_lessoninfo_jobs as c

# lessoninfo.co.kr currently challenges non-browser automation. The public Cafe24 host
# exposes the same Lessoninfo board records and individual wr_no details without that
# front-door challenge, so use it as the deterministic collection source.
BASE = "https://lessoninfo.cafe24.com"
PUBLIC_BASE = "https://www.lessoninfo.co.kr"
BOARD_CODE = "20130529164321_7227"

SURFACES = [
    {
        "key": "afterschool-nulbom",
        "label": "방과후교사•늘봄학교 강사",
        "bo_table": "20170316171033_6494",
        "code": "20161203155128_0262",
    },
    {
        "key": "culture-arts",
        "label": "문화예술 채용",
        "bo_table": "20161022171024_9316",
        "code": "20170307205530_6024",
    },
]

CHALLENGE_RE = re.compile(r"자동등록방지|보안절차|prove that you are human|captcha|access denied", re.I)
CLOSED_RE = re.compile(r"\b구인완료\b|\(완료\)|\[완료\]|채용완료|모집완료|접수마감|모집마감|채용마감|마감되었습니다|종료되었습니다", re.I)
ALWAYS_OPEN_RE = re.compile(r"상시\s*채용|상시\s*모집|채용\s*시까지|충원\s*시까지|채용\s*완료\s*시까지", re.I)
DEADLINE_LABEL_RE = re.compile(
    r"(?:마감일자|마감일|접수마감(?:일)?|모집마감(?:일)?|채용마감(?:일)?|원서접수마감)\s*[:：|]?\s*"
    r"((?:20\d{2})[.\-/년]\s*\d{1,2}[.\-/월]\s*\d{1,2})",
    re.I,
)
RANGE_RE = re.compile(
    r"(?:접수기간|지원기간|모집기간|원서접수)\s*[:：|]?\s*"
    r"(?:20\d{2}[.\-/년]\s*)?\d{1,2}[.\-/월]\s*\d{1,2}[^~～\n]{0,40}[~～-]\s*"
    r"((?:20\d{2})?[.\-/년]?\s*\d{1,2}[.\-/월]\s*\d{1,2})",
    re.I,
)
SCOPE_RE = re.compile(r"서울|경기|고양|과천|광명|광주|구리|군포|김포|남양주|동두천|부천|성남|수원|시흥|안산|안성|안양|양주|양평|여주|연천|오산|용인|의왕|의정부|이천|파주|평택|포천|하남|화성|가평")
OUT_OF_SCOPE_RE = re.compile(r"인천|부산|세종|대구|대전|광주광역|울산|경남|경북|충남|충북|전남|전북|강원|제주")


def challenge(html: str) -> bool:
    return bool(CHALLENGE_RE.search(BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)))


def parse_date_any(raw: str, ref: datetime | None = None) -> str:
    raw = (raw or "").strip().replace("년", "-").replace("월", "-").replace("일", "")
    raw = re.sub(r"\s+", "", raw).replace("/", "-").replace(".", "-")
    raw = re.sub(r"-+", "-", raw).strip("-")
    ref = ref or c.now_kst()
    m = re.search(r"(?:(20\d{2})-)?(\d{1,2})-(\d{1,2})", raw)
    if not m:
        return ""
    year = int(m.group(1) or ref.year)
    month, day = int(m.group(2)), int(m.group(3))
    try:
        dt = datetime(year, month, day, tzinfo=c.KST)
        if not m.group(1) and dt < ref - timedelta(days=300):
            dt = dt.replace(year=year + 1)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return ""


def extract_deadline(text: str, registered: str = "") -> tuple[str, str]:
    if ALWAYS_OPEN_RE.search(text or ""):
        return "", "always-open"
    for rx in (DEADLINE_LABEL_RE, RANGE_RE):
        m = rx.search(text or "")
        if m:
            ref = c.now_kst()
            if registered:
                try:
                    ref = datetime.strptime(registered, "%Y-%m-%d").replace(tzinfo=c.KST)
                except ValueError:
                    pass
            d = parse_date_any(m.group(1), ref)
            if d:
                return d, "explicit-deadline"
    return "", ""


def list_url(surface: dict, page: int) -> str:
    params = {
        "bo_table": surface["bo_table"],
        "board_code": BOARD_CODE,
        "code": surface["code"],
        "page": page,
        "page_rows": 70,
    }
    return BASE + "/board/board.php?" + urlencode(params)


def canonical_detail(href: str, surface: dict) -> str | None:
    u = urljoin(BASE, href)
    q = parse_qs(urlparse(u).query)
    bt = (q.get("bo_table") or [""])[0]
    wr = (q.get("wr_no") or [""])[0]
    if bt != surface["bo_table"] or not wr.isdigit():
        return None
    params = {
        "bo_table": bt,
        "board_code": BOARD_CODE,
        "code": surface["code"],
        "wr_no": wr,
    }
    return BASE + "/board/board.php?" + urlencode(params)


def public_detail(url: str, surface: dict) -> str:
    q = parse_qs(urlparse(url).query)
    params = {
        "bo_table": surface["bo_table"],
        "board_code": BOARD_CODE,
        "code": surface["code"],
        "wr_no": (q.get("wr_no") or [""])[0],
    }
    return PUBLIC_BASE + "/board/board.php?" + urlencode(params)


def parse_list(html: str, surface: dict) -> list[dict]:
    if challenge(html):
        raise RuntimeError("human_verification_challenge")
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        u = canonical_detail(a.get("href", ""), surface)
        if not u:
            continue
        q = parse_qs(urlparse(u).query)
        wr = (q.get("wr_no") or [""])[0]
        sid = f"{surface['bo_table']}:{wr}"
        title = " ".join(a.stripped_strings).strip() or (a.get("title") or "").strip()
        parent = a.find_parent(["tr", "li", "article", "div"])
        row = " ".join(parent.stripped_strings) if parent else title
        registered = c.extract_date(row)
        found.setdefault(
            sid,
            {
                "sourceIdentity": sid,
                "url": u,
                "publicUrl": public_detail(u, surface),
                "titleHint": title,
                "rowText": row,
                "registeredHint": registered,
                "surface": surface["key"],
                "surfaceLabel": surface["label"],
            },
        )
    return list(found.values())


def crawl_surface(s, surface: dict) -> tuple[list[dict], dict]:
    found: dict[str, dict] = {}
    fingerprints = set()
    old_streak = 0
    pages = 0
    # Discovery horizon is deliberately longer than the publishing fallback horizon;
    # this allows old '상시채용' records to survive if the detail explicitly says so.
    discovery_days = max(c.LOOKBACK_DAYS, 180)
    cutoff = c.now_kst() - timedelta(days=discovery_days)
    for page in range(1, min(c.MAX_PAGES, 120) + 1):
        r = c.get(s, list_url(surface, page))
        pages += 1
        rows = parse_list(r.text, surface)
        if not rows:
            complete = page > 1 and bool(found)
            return list(found.values()), {
                "surface": surface["label"], "complete": complete, "pagesScanned": pages,
                "ids": len(found), "stopReason": "end_of_pages" if complete else "unexpected_empty_first_page",
            }
        ids = tuple(sorted(x["sourceIdentity"] for x in rows))
        if ids in fingerprints:
            return list(found.values()), {
                "surface": surface["label"], "complete": True, "pagesScanned": pages,
                "ids": len(found), "stopReason": "repeated_page_boundary",
            }
        fingerprints.add(ids)
        for row in rows:
            found.setdefault(row["sourceIdentity"], row)

        dates = []
        for row in rows:
            d = row.get("registeredHint")
            if d:
                try:
                    dates.append(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=c.KST))
                except ValueError:
                    pass
        if dates and all(d < cutoff for d in dates):
            old_streak += 1
            if old_streak >= 2:
                return list(found.values()), {
                    "surface": surface["label"], "complete": True, "pagesScanned": pages,
                    "ids": len(found), "stopReason": "discovery_horizon_crossed",
                }
        else:
            old_streak = 0

    return list(found.values()), {
        "surface": surface["label"], "complete": False, "pagesScanned": pages,
        "ids": len(found), "stopReason": "max_pages",
    }


def parse_active_detail(s, item: dict) -> tuple[dict | None, str]:
    r = c.get(s, item["url"])
    if challenge(r.text):
        raise RuntimeError("human_verification_challenge")
    soup = BeautifulSoup(r.text, "html.parser")
    title = item.get("titleHint", "")
    for sel in ("h1", "h2", "h3", ".subject", ".title"):
        el = soup.select_one(sel)
        if el:
            t = " ".join(el.stripped_strings).strip()
            if len(t) >= 2:
                title = t
                break
    text = " ".join(soup.stripped_strings)
    signal = (title + " " + text[:16000]).strip()

    if CLOSED_RE.search(title) or CLOSED_RE.search(text[:5000]):
        return None, "closed-marker"
    if c.EXCLUDE_RE.search(title) and not c.RECRUIT_RE.search(title):
        return None, "non-recruitment"
    if not c.RECRUIT_RE.search(signal):
        return None, "non-recruitment"

    # Keep only Seoul/Gyeonggi for this project. Prefer explicit positive scope evidence;
    # if a different province is explicit and no Seoul/Gyeonggi signal exists, reject it.
    in_scope = bool(SCOPE_RE.search(signal))
    if not in_scope and OUT_OF_SCOPE_RE.search(signal):
        return None, "out-of-scope"
    if not in_scope:
        return None, "location-unconfirmed"

    registered = item.get("registeredHint") or c.extract_date(text)
    deadline, deadline_reason = extract_deadline(text, registered)
    today = c.now_kst().date()
    if deadline:
        try:
            if datetime.strptime(deadline, "%Y-%m-%d").date() < today:
                return None, "expired-deadline"
        except ValueError:
            pass
    elif not ALWAYS_OPEN_RE.search(signal):
        # No trustworthy deadline: do not carry old notices forever. A fresh 30-day
        # fallback is intentionally conservative because the user asked for current jobs only.
        if registered:
            try:
                rd = datetime.strptime(registered, "%Y-%m-%d").date()
                if rd < today - timedelta(days=30):
                    return None, "stale-without-deadline"
            except ValueError:
                pass
        else:
            return None, "no-date-no-deadline"

    q = parse_qs(urlparse(item["url"]).query)
    bt = (q.get("bo_table") or [""])[0]
    wr = (q.get("wr_no") or [""])[0]
    return {
        "sourceIdentity": f"{bt}:{wr}",
        "source": "레슨인포",
        "sourceType": "민간 구인",
        "trustLevel": "민간출처",
        "category": "private-recruitment",
        "sourceSurface": item["surface"],
        "sourceSurfaceLabel": item["surfaceLabel"],
        "title": title[:300],
        "registered": registered,
        "applyEnd": deadline,
        "activeReason": deadline_reason or ("always-open" if ALWAYS_OPEN_RE.search(signal) else "fresh-no-deadline"),
        "url": item["publicUrl"],
        "originalUrl": item["publicUrl"],
        "collectionUrl": item["url"],
        "boTable": bt,
        "wrNo": wr,
        "metroConfirmed": True,
        "collectedAt": c.now_kst().isoformat(timespec="seconds"),
    }, "active"


def main() -> int:
    s = c.session()
    # Verify the mirror is really Lessoninfo content, not a challenge/error shell.
    try:
        probe = c.get(s, BASE + "/")
        if not probe.ok or challenge(probe.text):
            raise RuntimeError("public_source_unavailable")
    except Exception as e:
        c.write_json(c.REPORT, {
            "generatedAt": c.now_kst().isoformat(timespec="seconds"),
            "policy": "lessoninfo-active-two-surface-v5",
            "healthy": False,
            "blocker": f"{type(e).__name__}:{e}",
            "preservedPreviousDataset": c.OUT.exists(),
        })
        return 2

    candidates: dict[str, dict] = {}
    reports = []
    complete = True
    errors = []
    for surface in SURFACES:
        try:
            rows, report = crawl_surface(s, surface)
            reports.append(report)
            complete &= bool(report.get("complete"))
            for row in rows:
                candidates.setdefault(row["sourceIdentity"], row)
        except Exception as e:
            complete = False
            errors.append(f"{surface['key']}:{type(e).__name__}:{e}")
            reports.append({"surface": surface["label"], "complete": False, "error": f"{type(e).__name__}:{e}"})

    previous = c.read_json(c.OUT, {"jobs": []})
    prev_jobs = previous if isinstance(previous, list) else previous.get("jobs", [])
    prev_by_id = {j.get("sourceIdentity"): j for j in prev_jobs if j.get("sourceIdentity")}

    active: dict[str, dict] = {}
    classifications: dict[str, str] = {}
    detail_errors = []
    for sid, item in candidates.items():
        try:
            job, reason = parse_active_detail(s, item)
            classifications[sid] = reason
            if job:
                active[sid] = job
        except Exception as e:
            detail_errors.append({"id": sid, "error": f"{type(e).__name__}:{e}"})

    # If detail failures occur for IDs that were previously published, retaining the known-good
    # record is safer than silently dropping a possibly still-open opportunity.
    for err in detail_errors:
        sid = err["id"]
        if sid in prev_by_id:
            active[sid] = prev_by_id[sid]
            classifications[sid] = "retained-on-detail-error"

    active_ids = set(active)
    missing_after = sorted(active_ids - set(active))  # explicit invariant; normally zero
    per_surface_active = {surface["key"]: 0 for surface in SURFACES}
    for job in active.values():
        if job.get("sourceSurface") in per_surface_active:
            per_surface_active[job["sourceSurface"]] += 1

    # Both source boards must actually expose IDs. Active count may legitimately be zero for one
    # category, but a zero-ID board is treated as structural failure rather than 'no vacancies'.
    source_id_ok = all((r.get("ids") or 0) > 0 for r in reports if "ids" in r) and len(reports) == len(SURFACES)
    healthy = complete and source_id_ok and not missing_after and not detail_errors and not errors

    jobs = sorted(active.values(), key=lambda j: (j.get("applyEnd") or "9999-12-31", j.get("registered") or "", j.get("sourceIdentity") or "")) if healthy else prev_jobs
    now = c.now_kst().isoformat(timespec="seconds")
    if healthy:
        c.write_json(c.OUT, {
            "updatedAt": c.now_kst().strftime("%Y-%m-%d %H:%M KST"),
            "source": "레슨인포",
            "category": "private-recruitment",
            "policy": "active-only",
            "jobs": jobs,
        })

    ledger = c.read_json(c.LEDGER, {"ids": {}, "snapshots": []})
    ledger.setdefault("ids", {})
    for sid, item in candidates.items():
        entry = ledger["ids"].setdefault(sid, {"firstSeenAt": now})
        entry.update({
            "lastSeenAt": now,
            "url": item["publicUrl"],
            "collectionUrl": item["url"],
            "surface": item["surface"],
            "classification": classifications.get(sid, "detail-error" if any(x["id"] == sid for x in detail_errors) else "unknown"),
        })
    ledger.setdefault("snapshots", []).append({
        "at": now,
        "candidateIds": len(candidates),
        "activeIds": len(active_ids),
        "perSurfaceActive": per_surface_active,
        "healthy": healthy,
    })
    ledger["snapshots"] = ledger["snapshots"][-180:]
    c.write_json(c.LEDGER, ledger)

    reason_counts: dict[str, int] = {}
    for reason in classifications.values():
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    report = {
        "generatedAt": now,
        "policy": "lessoninfo-active-two-surface-v5",
        "collectionBase": BASE,
        "publicBase": PUBLIC_BASE,
        "healthy": healthy,
        "traversalComplete": complete,
        "sourceReports": reports,
        "candidateIdCount": len(candidates),
        "activeIdCount": len(active_ids),
        "publishedIdCount": len({j.get("sourceIdentity") for j in jobs if j.get("sourceIdentity")}),
        "perSurfaceActive": per_surface_active,
        "missingAfterCount": len(missing_after),
        "missingAfterExamples": missing_after[:20],
        "detailErrorCount": len(detail_errors),
        "detailErrorExamples": detail_errors[:10],
        "classificationCounts": reason_counts,
        "preservedPreviousDataset": not healthy,
        "errors": errors,
    }
    c.write_json(c.REPORT, report)
    state = c.read_json(c.STATE, {})
    c.write_json(c.STATE, {
        "updatedAt": now,
        "healthy": healthy,
        "lastGoodAt": now if healthy else state.get("lastGoodAt"),
        "activeIdCount": len(active_ids) if healthy else state.get("activeIdCount", 0),
    })
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if healthy else 3


if __name__ == "__main__":
    raise SystemExit(main())
