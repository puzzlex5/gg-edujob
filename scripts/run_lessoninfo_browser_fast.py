#!/usr/bin/env python3
"""Fast and quality-hardened runner for the browser-rendered Lessoninfo collector.

The list pages are the authoritative discovery surface. Detail pages are used only to enrich the
specific discovered posting. Site-wide headings/sidebar text must never replace a list-row title
or make an old posting look current.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import crawl_lessoninfo_browser as b


async def fast_load(page, url: str):
    await page.goto(url, wait_until="domcontentloaded", timeout=35000)
    await page.wait_for_timeout(450)
    text = await b.visible_text(page)
    html = await page.content()
    if b.CHALLENGE_RE.search(text[:10000]):
        raise RuntimeError("human_verification_challenge")
    return html, text


b.load = fast_load

_BASE_CANDIDATE = b.candidate_from_anchor
GENERIC_TITLE_RE = re.compile(
    r"^(?:문화예술\s*일자리(?:\s*[\d,]+건)?|문화예술\s*채용|채용정보|구인정보|구인구직|레슨인포)\s*$",
    re.I,
)
REGISTERED_LABEL_RE = re.compile(
    r"(?:등록일(?:자)?|작성일(?:자)?|게시일(?:자)?|공고일(?:자)?)\s*[:：|]?\s*"
    r"((?:20\d{2}[.\-/년]\s*)?\d{1,2}[.\-/월]\s*\d{1,2})",
    re.I,
)


def _date_not_future(raw: str) -> str:
    d = b.parse_date(raw)
    if not d:
        return ""
    try:
        dt = datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        return ""
    return d if dt <= b.now_kst().date() else ""


def _list_registered(row: str, title: str) -> str:
    """Prefer a date outside the clickable title; titles often contain event/deadline dates."""
    body = row or ""
    if title:
        body = body.replace(title, " ", 1)
    matches = list(b.DATE_RE.finditer(body))
    for m in reversed(matches):
        d = _date_not_future(m.group(0))
        if d:
            return d
    return ""


def clean_candidate(href: str, text: str, row_text: str, target: dict):
    item = _BASE_CANDIDATE(href, text, row_text, target)
    if not item:
        return None
    item["registeredHint"] = _list_registered(item.get("rowText", ""), item.get("titleHint", ""))
    title = re.sub(r"\s+", " ", item.get("titleHint", "")).strip()
    # A generic page heading is navigation noise, not a recruitment title.
    if target.get("key") == "culture-arts" and GENERIC_TITLE_RE.fullmatch(title):
        return None
    return item


b.candidate_from_anchor = clean_candidate


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _detail_scope(html: str, title_hint: str) -> str:
    soup = b.BeautifulSoup(html, "html.parser")
    hint = _norm(title_hint)
    candidates: list[str] = []
    selectors = (
        "main", "article", "#content", "#contents", ".content", ".contents",
        ".view", ".view_content", ".view-content", ".board_view", ".board-view",
        ".detail", ".job_detail", ".job-detail", ".wr_content", ".bo_v_con",
    )
    for sel in selectors:
        for el in soup.select(sel):
            t = _norm(" ".join(el.stripped_strings))
            if 40 <= len(t) <= 30000 and (not hint or hint[:18] in t or hint in t):
                candidates.append(t)
    if hint:
        for node in soup.find_all(string=True):
            nt = _norm(str(node))
            if len(nt) < 3 or (hint not in nt and nt not in hint):
                continue
            parent = getattr(node, "parent", None)
            depth = 0
            while parent is not None and depth < 6:
                t = _norm(" ".join(parent.stripped_strings))
                if len(hint) + 20 <= len(t) <= 30000:
                    candidates.append(t)
                parent = parent.parent
                depth += 1
    if candidates:
        # The smallest container that still contains the discovered title is least likely to
        # include unrelated sidebar jobs, advertisements, or site-wide '상시채용' text.
        return min(candidates, key=len)
    body = soup.body
    if body:
        return _norm(" ".join(body.stripped_strings))[:12000]
    return ""


def _registered_from_scope(scope: str, fallback: str) -> str:
    m = REGISTERED_LABEL_RE.search(scope or "")
    if m:
        d = _date_not_future(m.group(1))
        if d:
            return d
    return _date_not_future(fallback)


def classify_clean(html: str, text: str, item: dict):
    title = _norm(item.get("titleHint", ""))
    if not title or GENERIC_TITLE_RE.fullmatch(title):
        return None, "generic-title"

    scope = _detail_scope(html, title)
    row = _norm(item.get("rowText", ""))
    signal = _norm(" ".join((title, row, scope)))

    if b.CLOSED_RE.search(title + " " + scope[:12000]):
        return None, "closed-marker"
    if b.EXCLUDE_RE.search(title) or (b.EXCLUDE_RE.search(row) and not b.RECRUIT_RE.search(title)):
        return None, "non-recruitment"
    if not b.RECRUIT_RE.search(signal):
        return None, "non-recruitment"

    registered = _registered_from_scope(scope, item.get("registeredHint", ""))
    deadline, deadline_reason = b.parse_deadline(scope, registered)
    today = b.now_kst().date()
    always_open = bool(b.ALWAYS_OPEN_RE.search(scope))

    if deadline:
        try:
            if datetime.strptime(deadline, "%Y-%m-%d").date() < today:
                return None, "expired-deadline"
        except ValueError:
            return None, "invalid-deadline"
    elif not always_open:
        if not registered:
            return None, "no-date-no-deadline"
        try:
            reg = datetime.strptime(registered, "%Y-%m-%d").date()
        except ValueError:
            return None, "invalid-date-no-deadline"
        if reg > today:
            return None, "future-registration-date"
        if reg < today - timedelta(days=b.NO_DEADLINE_FRESH_DAYS):
            return None, "stale-without-deadline"

    location = ""
    loc_signal = _norm(row + " " + scope[:12000])
    for rx in (
        re.compile(r"(?:지역|근무지역|소재지|기관주소|주소)\s*[:：|]?\s*([^|\n]{2,60})"),
        re.compile(r"((?:서울(?:특별시)?|경기(?:도)?|인천(?:광역시)?|부산(?:광역시)?|대구(?:광역시)?|대전(?:광역시)?|광주(?:광역시)?|울산(?:광역시)?|세종(?:특별자치시)?|강원(?:특별자치도)?|충북|충남|전북|전남|경북|경남|제주)\s*\S{0,18})"),
    ):
        m = rx.search(loc_signal)
        if m:
            location = _norm(m.group(1))[:80]
            break

    return {
        "sourceIdentity": item["sourceIdentity"],
        "source": "레슨인포",
        "sourceType": "민간 구인",
        "trustLevel": "민간출처",
        "category": "private-recruitment",
        "sourceSurface": item["sourceSurface"],
        "sourceSurfaceLabel": item["sourceSurfaceLabel"],
        "title": title[:300],
        "registered": registered,
        "applyEnd": deadline,
        "activeReason": deadline_reason or ("always-open" if always_open else "fresh-no-deadline"),
        "location": location,
        "url": item["url"],
        "originalUrl": item["url"],
        "collectedAt": b.now_kst().isoformat(timespec="seconds"),
    }, "active"


b.classify_detail = classify_clean

if __name__ == "__main__":
    raise SystemExit(b.main())
