#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
TARGETS = {
    "cleaneye": "https://job.cleaneye.go.kr/user/ypRecruitment.do",
    "ancf": "https://www.ancf.or.kr/",
    "ancf_members": "https://www.ancf.or.kr/members/state",
}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36"


def compact(s: str) -> str:
    return " ".join((s or "").split())[:500]


def inspect(url: str) -> dict:
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}, timeout=30)
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
            ][:200],
        })
    anchors = []
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a.get("href"))
        txt = compact(a.get_text(" ", strip=True))
        if any(k in (href + " " + txt).lower() for k in ["recruit", "career", "채용", "사람", "member", "ypcareers", "yprecruit"]):
            anchors.append({"text": txt, "href": href})
    scripts = [urljoin(url, s.get("src")) for s in soup.find_all("script", src=True)]
    return {
        "url": url,
        "status": r.status_code,
        "finalUrl": r.url,
        "contentType": r.headers.get("content-type", ""),
        "title": compact(soup.title.get_text(" ", strip=True) if soup.title else ""),
        "forms": forms,
        "anchors": anchors[:100],
        "scripts": scripts[:100],
    }


def main() -> int:
    out = {"generatedAt": datetime.now(KST).isoformat(timespec="seconds"), "targets": {}, "errors": []}
    for key, url in TARGETS.items():
        try:
            out["targets"][key] = inspect(url)
        except Exception as exc:
            out["errors"].append({"source": key, "error": f"{type(exc).__name__}: {exc}"})
    Path("cultural_source_probe_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"targets": {k: {"status": v.get("status"), "forms": len(v.get("forms", [])), "anchors": len(v.get("anchors", []))} for k, v in out["targets"].items()}, "errors": out["errors"]}, ensure_ascii=False, indent=2))
    return 0 if out["targets"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
