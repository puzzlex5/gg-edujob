#!/usr/bin/env python3
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SOURCES = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
OUT = ROOT / "support_link_diagnostics.json"
S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; metro-edujob-link-diagnostic/1.0)",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
})


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def get(url, params=None):
    try:
        r = S.get(url, params=params, timeout=20, allow_redirects=True)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        return r
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:160]}"}


def row_snapshot(tr):
    anchors = []
    for a in tr.find_all("a"):
        attrs = {}
        for k, v in a.attrs.items():
            if k in ("href", "onclick", "id", "name", "class", "data-ntt-sn", "data-nttsn", "data-seq", "data-id") or k.startswith("data-"):
                attrs[k] = v
        anchors.append({"text": clean(a.get_text(" ", strip=True))[:180], "attrs": attrs})
    inputs = []
    for x in tr.find_all("input"):
        inputs.append({k: v for k, v in x.attrs.items() if k in ("name", "id", "value", "type") or k.startswith("data-")})
    return {
        "text": clean(tr.get_text(" ", strip=True))[:600],
        "rowAttrs": dict(tr.attrs),
        "anchors": anchors[:8],
        "inputs": inputs[:8],
        "html": str(tr)[:3000],
    }


def table_rows(soup, limit=4):
    result = []
    for table in soup.find_all("table"):
        header_row = table.find("tr")
        headers = [clean(x.get_text(" ", strip=True)) for x in header_row.find_all("th")] if header_row else []
        rows = []
        for tr in table.find_all("tr"):
            if tr.find_all("td"):
                rows.append(row_snapshot(tr))
                if len(rows) >= limit:
                    break
        if rows:
            result.append({"headers": headers, "rows": rows})
        if len(result) >= 3:
            break
    return result


def diag_gyeonggi(src):
    boards = []
    for board in src.get("boardUrls", []):
        p = urlparse(board)
        q = parse_qs(p.query, keep_blank_values=True)
        q["currPage"] = ["1"]
        page_url = urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q, doseq=True), p.fragment))
        r = get(page_url)
        if isinstance(r, dict):
            boards.append({"board": board, **r})
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        boards.append({
            "board": board,
            "resolved": r.url,
            "status": r.status_code,
            "title": clean(soup.title.get_text(" ", strip=True) if soup.title else ""),
            "tables": table_rows(soup),
        })
    return {"name": src["name"], "boards": boards}


def diag_seoul(src):
    board = src["boardUrl"]
    r = get(board, params={"pageIndex": "1"})
    if isinstance(r, dict):
        return {"name": src["name"], "board": board, **r}
    soup = BeautifulSoup(r.text, "html.parser")
    scripts = []
    for sc in soup.find_all("script"):
        text = sc.get_text("\n", strip=True)
        if "fncDetailView" in text or "JOV11" in text or "job_seq" in text:
            scripts.append(text[:3000])
    return {
        "name": src["name"],
        "board": board,
        "resolved": r.url,
        "status": r.status_code,
        "title": clean(soup.title.get_text(" ", strip=True) if soup.title else ""),
        "tables": table_rows(soup),
        "detailScripts": scripts[:3],
    }


def main():
    data = {
        "gyeonggi": [diag_gyeonggi(x) for x in SOURCES["gyeonggi"]["supportOffices"]],
        "seoul": [diag_seoul(x) for x in SOURCES["seoul"]["supportOffices"]],
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT, "GYEONGGI", len(data["gyeonggi"]), "SEOUL", len(data["seoul"]))


if __name__ == "__main__":
    main()
