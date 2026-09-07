#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

KST = timezone(timedelta(hours=9))
REGISTRY = Path("cultural_foundation_registry.json")
ROUTES = Path("cultural_foundation_routes.json")

RESULT_WORDS = re.compile(r"(최종\s*합격|합격자|서류\s*전형|면접\s*전형|임용\s*후보|전형\s*결과|결과\s*공고)", re.I)
JOB_WORDS = re.compile(r"(채용|직원\s*모집|근로자\s*모집|인력\s*모집|사무국장\s*모집|임원\s*공개모집)", re.I)
DATE_RE = re.compile(r"(20\d{2})[.\-/년\s]+(\d{1,2})[.\-/월\s]+(\d{1,2})")
EXTRA_ALIASES = {
    "gyeonggi:pocheon": ["포천문화관광재단", "(재)포천문화관광재단", "재단법인 포천문화관광재단"],
    "gyeonggi:yeoju": ["여주세종문화관광재단", "여주세종문화재단"],
    "gyeonggi:hwaseong": ["화성시문화관광재단", "화성시문화재단", "화성문화재단"],
}


def norm(text: str) -> str:
    text = re.sub(r"\(\s*재\s*\)|\[\s*재\s*\]|재단법인", "", str(text or ""), flags=re.I)
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", text).lower()


def load_foundations() -> list[dict]:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    route_data = json.loads(ROUTES.read_text(encoding="utf-8"))
    routes = route_data.get("routes", {})
    out = []
    for raw in data.get("institutions", []):
        if raw.get("enabled") is False:
            continue
        row = dict(raw)
        overlay = routes.get(str(row.get("id") or ""), {})
        row["homepage"] = str(overlay.get("homepage") or row.get("homepage") or "").strip()
        row["officialRecruitmentUrl"] = str(overlay.get("recruitmentUrl") or row.get("officialRecruitmentUrl") or "").strip()
        extras = EXTRA_ALIASES.get(str(row.get("id") or ""), [])
        row["aliases"] = list(dict.fromkeys([*(row.get("aliases") or []), *extras]))
        if row.get("id") == "gyeonggi:pocheon":
            row["canonicalCurrentName"] = "포천문화관광재단"
        out.append(row)
    return out


def alias_index(foundations: list[dict] | None = None):
    foundations = foundations or load_foundations()
    pairs = []
    for row in foundations:
        for value in [row.get("name"), row.get("canonicalCurrentName"), *(row.get("aliases") or [])]:
            n = norm(value)
            if n:
                pairs.append((n, row))
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    return pairs


def match_foundation(text: str, pairs=None):
    target = norm(text)
    for alias, row in (pairs or alias_index()):
        if alias and alias in target:
            return row
    return None


def is_recruitment_title(title: str) -> bool:
    title = str(title or "")
    return bool(JOB_WORDS.search(title)) and not bool(RESULT_WORDS.search(title))


def iso_date_from_text(text: str) -> str:
    matches = DATE_RE.findall(str(text or ""))
    for y, m, d in matches:
        try:
            return date(int(y), int(m), int(d)).isoformat()
        except Exception:
            continue
    return ""


def same_origin(url_a: str, url_b: str) -> bool:
    try:
        a = (urlparse(url_a).hostname or "").lower().removeprefix("www.")
        b = (urlparse(url_b).hostname or "").lower().removeprefix("www.")
        return bool(a and b and a == b)
    except Exception:
        return False


def generated_at() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")
