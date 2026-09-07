#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
KST = timezone(timedelta(hours=9))
TARGETS = {
    "cleaneye": ("https://job.cleaneye.go.kr/user/ypRecruitment.do", True),
    "ancf": ("https://www.ancf.or.kr/", False),
    "ancf_members": ("https://www.ancf.or.kr/members/state", False),
}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36"
KEYWORDS = (
    "searchentnamebtn", "searchkeyword", "modalsearchform", "ypcareersdata",
    "yprecruitment", "ajax", "recruit", "채용", "사람", "members/state",
)


def compact(s: str) -> str:
    return " ".join((s or "").split())[:500]


def relevant_js_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        lo = line.lower()
        if any(k in lo for k in KEYWORDS):
            lines.append(line[:1000])
    return lines[:300]


def inspect(url: str, verify_tls: bool) -> dict:
    r = requests.get(
        url,
        headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"},
        timeout=30,
        verify=verify_tls,
    )
    soup = BeautifulSoup(r.text, "html.parser")
    forms = []
    for form in soup.find_all("form"):
        forms.append({
            "id": form.get("id"),
            "name": form.get("name"),
            "method": (form.get("method") or "get").lower(),
            "action": urljoin(url, form.get("action") or ""),
            "text": compact(form.get_text(" ", strip=True)),
            "inputs": [
                {
                    "tag": x.name,
                    "type": x.get("type"),
                    "id": x.get("id"),
                    "name": x.get("name"),
                    "value": x.get("value"),
                    "placeholder": x.get("placeholder"),
                }
                for x in form.find_all(["input", "select", "button", "textarea"])
            ][:300],
        })
    anchors = []
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a.get("href"))
        txt = compact(a.get_text(" ", strip=True))
        if any(k in (href + " " + txt).lower() for k in ["recruit", "career", "채용", "사람", "member", "ypcareers", "yprecruit"]):
            anchors.append({"text": txt, "href": href})
    scripts = [urljoin(url, s.get("src")) for s in soup.find_all("script", src=True)]
    js_hits = {}
    for src in scripts[:50]:
        try:
            sr = requests.get(src, headers={"User-Agent": UA}, timeout=20, verify=verify_tls)
            hits = relevant_js_lines(sr.text)
            if hits:
                js_hits[src] = hits
        except Exception:
            pass
    inline_hits = relevant_js_lines("\n".join(s.get_text("\n") for s in soup.find_all("script") if not s.get("src")))
    return {
        "url": url,
        "status": r.status_code,
        "finalUrl": r.url,
        "contentType": r.headers.get("content-type", ""),
        "title": compact(soup.title.get_text(" ", strip=True) if soup.title else ""),
        "tlsVerified": verify_tls,
        "forms": forms,
        "anchors": anchors[:150],
        "scripts": scripts[:100],
        "scriptHits": js_hits,
        "inlineScriptHits": inline_hits,
        "rawEndpointCandidates": sorted(set(re.findall(r"['\"]([^'\"]+(?:\.do|/api/[^'\"]+))['\"]", r.text)))[:200],
    }


def main() -> int:
    out = {"generatedAt": datetime.now(KST).isoformat(timespec="seconds"), "targets": {}, "errors": []}
    for key, (url, verify_tls) in TARGETS.items():
        try:
            out["targets"][key] = inspect(url, verify_tls)
        except Exception as exc:
            out["errors"].append({"source": key, "error": f"{type(exc).__name__}: {exc}"})
    Path("cultural_source_probe_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "targets": {
            k: {
                "status": v.get("status"),
                "forms": len(v.get("forms", [])),
                "anchors": len(v.get("anchors", [])),
                "scriptHitFiles": len(v.get("scriptHits", {})),
                "inlineHits": len(v.get("inlineScriptHits", [])),
            }
            for k, v in out["targets"].items()
        },
        "errors": out["errors"],
    }, ensure_ascii=False, indent=2))
    return 0 if out["targets"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
