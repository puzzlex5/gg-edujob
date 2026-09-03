#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
BASE = "https://www.jobteacher.kr"
JOBS_PATH = Path("jobteacher_jobs.json")
LEDGER_PATH = Path("jobteacher_detail_link_ledger.json")
REPORT_PATH = Path("jobteacher_detail_link_report.json")
BATCH = 4
ROTATING_SAMPLE = 60


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def sample_verified(ids, n):
    if len(ids) <= n:
        return ids
    step = max(1, len(ids) // n)
    out = ids[::step][:n]
    return out


def main():
    raw = load(JOBS_PATH, [])
    jobs = raw if isinstance(raw, list) else raw.get("jobs", [])
    old = load(LEDGER_PATH, {"entries": {}})
    entries = old.get("entries", {}) if isinstance(old, dict) else {}
    by_id = {str(j.get("sourceIdentity") or ""): j for j in jobs if j.get("sourceIdentity") and j.get("url")}
    current_ids = sorted(by_id)
    unverified = [sid for sid in current_ids if not (entries.get(sid) or {}).get("verified")]
    previously_verified = [sid for sid in current_ids if (entries.get(sid) or {}).get("verified")]
    targets = list(dict.fromkeys(unverified + sample_verified(previously_verified, ROTATING_SAMPLE)))

    results = {}
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ko-KR")
        page = ctx.new_page()
        resp = page.goto(f"{BASE}/employ/lists", wait_until="domcontentloaded", timeout=45000)
        if not resp or resp.status != 200:
            raise RuntimeError(f"JobTeacher bootstrap failed: {resp.status if resp else 0}")
        for i in range(0, len(targets), BATCH):
            chunk = targets[i:i+BATCH]
            payload = [{"sid": sid, "url": by_id[sid]["url"]} for sid in chunk]
            batch = page.evaluate("""async (items) => {
              const check = async (item) => {
                try {
                  let r = await fetch(item.url, {method:'HEAD', redirect:'follow', credentials:'omit', cache:'no-store'});
                  if (r.status === 405 || r.status === 403 || r.status === 400) {
                    r = await fetch(item.url, {method:'GET', headers:{'Range':'bytes=0-1023'}, redirect:'follow', credentials:'omit', cache:'no-store'});
                  }
                  return {sid:item.sid, ok:r.ok, status:r.status, finalUrl:r.url};
                } catch (e) {
                  return {sid:item.sid, ok:false, status:0, finalUrl:'', error:String(e)};
                }
              };
              return await Promise.all(items.map(check));
            }""", payload)
            for r in batch:
                sid = r.get("sid")
                expected = by_id[sid]["url"]
                final_url = str(r.get("finalUrl") or "")
                ok = bool(r.get("ok")) and final_url.startswith(f"{BASE}/employ/detail/")
                results[sid] = {**r, "ok": ok, "expectedUrl": expected}
                if not ok:
                    errors.append(results[sid])
            page.wait_for_timeout(150)
        ctx.close(); browser.close()

    now = datetime.now(KST).isoformat(timespec="seconds")
    for sid in current_ids:
        ent = dict(entries.get(sid) or {})
        ent.update({"url": by_id[sid]["url"], "presentInLatestScan": True})
        if sid in results:
            r = results[sid]
            ent.update({"verified": bool(r.get("ok")), "status": int(r.get("status") or 0), "finalUrl": r.get("finalUrl") or "", "verifiedAt": now})
        entries[sid] = ent
    for sid, ent in list(entries.items()):
        if sid not in by_id and isinstance(ent, dict):
            ent["presentInLatestScan"] = False

    verified_current = [sid for sid in current_ids if (entries.get(sid) or {}).get("verified")]
    missing_coverage = sorted(set(current_ids) - set(verified_current))
    healthy = bool(current_ids) and not errors and not missing_coverage
    ledger = {"updatedAt": now, "source": "잡티처", "entries": entries}
    report = {
        "generatedAt": now,
        "source": "잡티처",
        "policy": "jobteacher-detail-link-browser-v1",
        "healthy": healthy,
        "currentIdCount": len(current_ids),
        "checkedThisRun": len(targets),
        "newlyCheckedThisRun": len(unverified),
        "verifiedCurrentCount": len(verified_current),
        "detailCoverageComplete": len(missing_coverage) == 0,
        "missingDetailCoverageCount": len(missing_coverage),
        "missingDetailCoverage": missing_coverage[:100],
        "detailErrorCount": len(errors),
        "detailErrors": errors[:50],
        "transport": "chromium-browser-fetch-verified-tls",
        "tlsVerificationDisabled": False,
    }
    LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
