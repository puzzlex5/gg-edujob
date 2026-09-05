#!/usr/bin/env python3
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

GENERIC_PATH_RE = re.compile(
    r"(?:/main(?:\.[a-z]+)?$|/index(?:\.[a-z]+)?$|/search(?:/|$)|/lists?(?:/|$)|"
    r"selectNttList\.do$|/FUS/JO/JOL11\.do$|/recruitments/?$|search_list\.do$)",
    re.I,
)


def _params(url: str):
    try:
        return parse_qs(urlparse(url).query)
    except Exception:
        return {}


def _digits(value) -> str:
    s = str(value or "")
    return s if s.isdigit() else ""


def _is_seoul_cms_detail(parsed) -> bool:
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    return bool(
        (host == "sen.go.kr" or host.endswith(".sen.go.kr"))
        and re.search(r"/CMS/.+/\d+_\d+\.html$", path, re.I)
    )


def is_direct_post(job: dict) -> tuple[bool, str]:
    url = str(job.get("url") or job.get("originalUrl") or "").strip()
    sid = str(job.get("sourceIdentity") or "")
    source = str(job.get("source") or "")
    p = urlparse(url) if url else urlparse("")
    q = _params(url)
    path = p.path or ""

    if str(job.get("openMethod") or "").upper() == "POST":
        seq = _digits((job.get("openParams") or {}).get("job_seq"))
        if seq and job.get("openUrl"):
            return True, "seoul-post-job-seq"

    if source == "아트모아" or sid.startswith("artmore:"):
        rec = _digits((q.get("rec_idx") or [""])[0])
        return bool(rec and path.endswith("/search_view.do")), "artmore-rec-idx"

    if source == "레슨인포" or sid.startswith(("culture:id:", "board:")):
        ident = _digits((q.get("id") or [""])[0])
        wr_no = _digits((q.get("wr_no") or [""])[0])
        if path.endswith("/culture-jobs/detail.php") and ident:
            return True, "lessoninfo-culture-id"
        if path.endswith("/board/board.php") and wr_no:
            return True, "lessoninfo-wr-no"
        return False, "lessoninfo-generic-or-missing-id"

    if source == "잡티처" or sid.startswith("jobteacher:"):
        return bool(re.search(r"/employ/detail/\d+(?:/|$)", path)), "jobteacher-detail-id"

    if source == "공공강사" or sid.startswith("gonggonggangsa:"):
        return bool(re.search(r"/recruitments/\d+(?:/|$)", path)), "gonggonggangsa-detail-id"

    if sid.startswith("goe-central:"):
        pb = _digits((q.get("pbancSn") or [""])[0])
        return bool(pb and not GENERIC_PATH_RE.search(path)), "goe-pbancSn"

    if sid.startswith("seoul-central:"):
        rid = _digits((q.get("q_rcrtSn") or q.get("rcrtSn") or [""])[0])
        exact_detail_path = path.endswith("/work/search/recInfo/BD_selectRecDetail.do")
        return bool(rid and exact_detail_path), "seoul-central-rcrtSn"

    if sid.startswith("mircms:"):
        bbs = _digits((q.get("bbsId") or [job.get("bbsId") or ""])[0])
        ntt = _digits((q.get("nttSn") or [job.get("nttSn") or ""])[0])
        return bool(path.endswith("selectNttInfo.do") and bbs and ntt), "gyeonggi-bbs-ntt"

    if sid.startswith("seoul:"):
        seq = _digits((q.get("job_seq") or [(job.get("openParams") or {}).get("job_seq") or ""])[0])
        return bool(seq and not GENERIC_PATH_RE.search(path)), "seoul-job-seq"

    # Seoul support-office CMS postings use persistent numeric .html detail URLs.
    # Some unified rows intentionally preserve them under an official-url:* stable
    # identity, so classify by the trusted sen.go.kr host + exact CMS detail shape
    # rather than relying on one sourceIdentity prefix.
    if _is_seoul_cms_detail(p):
        return True, "seoul-cms-detail"

    if not url:
        return False, "missing-url"
    if GENERIC_PATH_RE.search(path):
        return False, "generic-list-or-home"
    if job.get("boardUrl") and str(job.get("boardUrl")) == url:
        return False, "url-equals-board-url"
    return True, "unclassified-nongeneric-url"


def recover_direct_post(job: dict) -> tuple[str, str]:
    """Return a safely reconstructed exact-detail URL when stable evidence is sufficient."""
    url = str(job.get("url") or job.get("originalUrl") or "").strip()
    sid = str(job.get("sourceIdentity") or "")
    source = str(job.get("source") or "")
    p = urlparse(url) if url else urlparse("")

    if source == "아트모아" or sid.startswith("artmore:"):
        ident = _digits(sid.rsplit(":", 1)[-1])
        if ident:
            return f"https://www.artmore.kr/sub/recruit/search_view.do?rec_idx={ident}", "artmore-source-id"

    if source == "잡티처" or sid.startswith("jobteacher:"):
        ident = _digits(sid.rsplit(":", 1)[-1])
        if ident:
            host = p.netloc or "www.jobteacher.co.kr"
            scheme = p.scheme or "https"
            return f"{scheme}://{host}/employ/detail/{ident}", "jobteacher-source-id"

    if source == "공공강사" or sid.startswith("gonggonggangsa:"):
        ident = _digits(sid.rsplit(":", 1)[-1])
        if ident and p.netloc:
            return f"{p.scheme or 'https'}://{p.netloc}/recruitments/{ident}", "gonggonggangsa-source-id"

    if sid.startswith("mircms:"):
        parts = sid.split(":")
        if len(parts) >= 4:
            host, bbs, ntt = parts[-3], _digits(parts[-2]), _digits(parts[-1])
            if host and bbs and ntt:
                mi = str(job.get("mi") or "")
                query = {"bbsId": bbs, "nttSn": ntt}
                if mi:
                    query["mi"] = mi
                path = p.path or str(urlparse(str(job.get("boardUrl") or "")).path)
                if path.endswith("selectNttList.do"):
                    path = path[:-len("selectNttList.do")] + "selectNttInfo.do"
                if path.endswith("selectNttInfo.do"):
                    return urlunparse((p.scheme or "https", p.netloc or host, path, "", urlencode(query), "")), "mircms-stable-id"

    if sid.startswith("culture:id:"):
        ident = _digits(sid.rsplit(":", 1)[-1])
        if ident and p.netloc:
            return f"{p.scheme or 'https'}://{p.netloc}/culture-jobs/detail.php?id={ident}", "lessoninfo-culture-source-id"

    if sid.startswith("board:"):
        ident = _digits(sid.rsplit(":", 1)[-1])
        if ident and p.netloc:
            return f"{p.scheme or 'https'}://{p.netloc}/board/board.php?wr_no={ident}", "lessoninfo-board-source-id"

    return "", "not-safely-recoverable"


def repair_and_mark(job: dict) -> dict:
    out = dict(job)
    ok, reason = is_direct_post(out)
    if not ok:
        recovered, recovery_reason = recover_direct_post(out)
        if recovered:
            out["url"] = recovered
            if out.get("feedKind") == "private":
                out["originalUrl"] = recovered
            ok, reason = is_direct_post(out)
            if ok:
                reason = recovery_reason
    out["detailLinkVerified"] = bool(ok)
    out["detailLinkReason"] = reason
    return out
