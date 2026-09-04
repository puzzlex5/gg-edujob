#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

KST = timezone(timedelta(hours=9))
BLOCK_RE = re.compile(r"captcha|사람인지|자동입력|비정상적인\s*접근|접근이\s*제한", re.I)
CANDIDATE_JOBS = Path("artmore_jobs.candidate.json")
CANDIDATE_LEDGER = Path("artmore_source_id_ledger.candidate.json")
CANDIDATE_REPORT = Path("artmore_reconciliation_report.candidate.json")
CANDIDATE_STATE = Path("artmore_collection_state.candidate.json")
DETAIL_REPORT = Path("artmore_detail_link_report.json")
CANONICAL_LEDGER = Path("artmore_source_id_ledger.json")
CANONICAL_JOBS = Path("artmore_jobs.json")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def stable_id_from_url(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    vals = q.get("rec_idx") or []
    return str(vals[0]) if vals else ""


def stable_ids_from_ledger(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = load(path)
    if isinstance(data, dict):
        for key in ("stableIds", "ids", "sourceIds"):
            vals = data.get(key)
            if isinstance(vals, list):
                return {str(x) for x in vals if str(x)}
        rows = data.get("rows") or data.get("items") or data.get("jobs")
        if isinstance(rows, list):
            return {
                str(x.get("stableId") or x.get("id") or "")
                for x in rows if isinstance(x, dict) and str(x.get("stableId") or x.get("id") or "")
            }
    if isinstance(data, list):
        return {
            str(x.get("stableId") or x.get("id") or x) if isinstance(x, dict) else str(x)
            for x in data
            if x
        }
    return set()


def canonical_count() -> int:
    ids = stable_ids_from_ledger(CANONICAL_LEDGER)
    if ids:
        return len(ids)
    if CANONICAL_JOBS.exists():
        data = load(CANONICAL_JOBS)
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        return len(jobs)
    return 0


def abnormal_drop(candidate_count: int, previous_count: int) -> tuple[bool, float]:
    if candidate_count <= 0:
        return True, 1.0 if previous_count else 0.0
    if previous_count < 10:
        return False, max(0.0, (previous_count - candidate_count) / previous_count) if previous_count else 0.0
    drop_ratio = max(0.0, (previous_count - candidate_count) / previous_count)
    # A verified public source should not lose >=40% of its active corpus in one promotion cycle.
    # Preserve the last known-good canonical dataset and require a later healthy run instead.
    return candidate_count < previous_count * 0.60, drop_ratio


async def validate_links(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    verified, errors = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(locale="ko-KR")
        for job in jobs:
            sid = str(job.get("stableId") or "")
            url = str(job.get("originalUrl") or job.get("url") or "")
            try:
                if not sid or stable_id_from_url(url) != sid:
                    raise RuntimeError("stable ID does not match rec_idx URL")
                response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                status = response.status if response else 0
                body = await page.locator("body").inner_text()
                if status != 200:
                    raise RuntimeError(f"HTTP {status}")
                if BLOCK_RE.search(body):
                    raise RuntimeError("human-check/block page detected")
                if len(body.strip()) < 200:
                    raise RuntimeError("detail body unexpectedly short")
                if stable_id_from_url(page.url) != sid:
                    raise RuntimeError(f"detail redirected away from rec_idx={sid}: {page.url}")
                verified.append({"sourceIdentity": job.get("sourceIdentity"), "stableId": sid, "url": page.url, "httpStatus": status})
            except Exception as exc:
                errors.append({"sourceIdentity": job.get("sourceIdentity"), "stableId": sid, "url": url, "error": f"{type(exc).__name__}: {exc}"})
        await browser.close()
    return verified, errors


async def main() -> int:
    generated = datetime.now(KST).isoformat(timespec="seconds")
    required = [CANDIDATE_JOBS, CANDIDATE_LEDGER, CANDIDATE_REPORT, CANDIDATE_STATE]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise SystemExit(f"missing candidate files: {missing}")

    candidate_report = load(CANDIDATE_REPORT)
    data = load(CANDIDATE_JOBS)
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    preconditions = bool(
        candidate_report.get("healthy")
        and candidate_report.get("traversalComplete")
        and candidate_report.get("missingAfterCount") == 0
        and jobs
    )
    if not preconditions:
        raise SystemExit("ArtMore candidate reconciliation gate is not healthy")

    previous_count = canonical_count()
    drop_blocked, drop_ratio = abnormal_drop(len(jobs), previous_count)
    if drop_blocked:
        detail_report = {
            "generatedAt": generated,
            "source": "아트모아",
            "healthy": False,
            "detailCoverageComplete": False,
            "candidateCount": len(jobs),
            "verifiedCount": 0,
            "detailErrorCount": 0,
            "abnormalDrop": True,
            "previousCanonicalCount": previous_count,
            "candidateDropRatio": round(drop_ratio, 4),
            "preservedPublishedDataset": True,
            "errors": [{"error": "abnormal candidate drop; canonical publication preserved"}],
            "verifiedExamples": [],
        }
        DETAIL_REPORT.write_text(json.dumps(detail_report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(detail_report, ensure_ascii=False, indent=2))
        return 3

    verified, errors = await validate_links(jobs)
    healthy = not errors and len(verified) == len(jobs)
    detail_report = {
        "generatedAt": generated,
        "source": "아트모아",
        "healthy": healthy,
        "detailCoverageComplete": healthy,
        "candidateCount": len(jobs),
        "verifiedCount": len(verified),
        "detailErrorCount": len(errors),
        "abnormalDrop": False,
        "previousCanonicalCount": previous_count,
        "candidateDropRatio": round(drop_ratio, 4),
        "errors": errors,
        "verifiedExamples": verified[:20],
    }
    DETAIL_REPORT.write_text(json.dumps(detail_report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not healthy:
        print(json.dumps(detail_report, ensure_ascii=False, indent=2))
        return 2

    # Promote only after all gates pass. Existing canonical files remain untouched on any failure.
    shutil.copyfile(CANDIDATE_JOBS, "artmore_jobs.json")
    shutil.copyfile(CANDIDATE_LEDGER, "artmore_source_id_ledger.json")

    canonical_report = dict(candidate_report)
    canonical_report.update({
        "generatedAt": generated,
        "policy": "artmore-active-browser-v1",
        "publicationEnabled": True,
        "detailErrorCount": 0,
        "abnormalDrop": False,
        "previousCanonicalCount": previous_count,
        "candidateDropRatio": round(drop_ratio, 4),
        "nextGate": "unified multi-source validation",
    })
    Path("artmore_reconciliation_report.json").write_text(json.dumps(canonical_report, ensure_ascii=False, indent=2), encoding="utf-8")

    state = load(CANDIDATE_STATE)
    state.update({
        "generatedAt": generated,
        "healthy": True,
        "promoted": True,
        "canonicalJobs": len(jobs),
        "detailCoverageComplete": True,
        "abnormalDrop": False,
        "previousCanonicalCount": previous_count,
        "candidateDropRatio": round(drop_ratio, 4),
    })
    Path("artmore_collection_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"healthy": True, "promoted": len(jobs), "detailErrors": 0, "abnormalDrop": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
