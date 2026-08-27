#!/usr/bin/env python3
"""Patch source-ID reconciliation to use the 90-day Gyeonggi central crawler.

This is idempotent and deliberately limited to reconciliation. The fast collector may keep the
official UI's active-oriented view, while the completeness proof must include recently closed
postings throughout the 90-day retention window. The patch also stamps a policy version into
all reconciliation artifacts so pre-90-day reports cannot be reused as authoritative evidence.

It also hardens Seoul support-office fallback registration dates. Some Seoul tables omit a real
등록일/작성일 column; using the last visible row date can select a future work/apply date and
corrupt the 90-day traversal boundary. Reconciliation must never treat a future date as a
registration date.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "reconcile_source_ids.py"
SUPPORT_CRAWLER = ROOT / "scripts" / "complete_support_coverage.py"
POLICY = "stable-id-38-v2-gyeonggi-central-90d"


def harden_seoul_registration_fallback():
    text = SUPPORT_CRAWLER.read_text(encoding="utf-8")
    seoul_pos = text.find("def seoul_board")
    if seoul_pos < 0:
        raise SystemExit("complete_support_coverage.py Seoul crawler not found")

    head, tail = text[:seoul_pos], text[seoul_pos:]
    unsafe_main = '''                if not registered:\n                    ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                    registered = ds[-1] if ds else ""'''
    safe_main = '''                if not registered:\n                    ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                    today_s = NOW.strftime("%Y/%m/%d")\n                    plausible = [d for d in ds if d and d <= today_s]\n                    registered = plausible[-1] if plausible else ""'''
    unsafe_post = '''                            if not registered:\n                                ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                                registered = ds[-1] if ds else ""'''
    safe_post = '''                            if not registered:\n                                ds = all_dates(clean(tr.get_text(" ", strip=True)))\n                                today_s = NOW.strftime("%Y/%m/%d")\n                                plausible = [d for d in ds if d and d <= today_s]\n                                registered = plausible[-1] if plausible else ""'''

    tail = tail.replace(unsafe_main, safe_main).replace(unsafe_post, safe_post)
    if 'registered = ds[-1] if ds else ""' in tail:
        raise SystemExit("Unsafe Seoul row-date fallback still present after reconciliation hardening")
    if "plausible = [d for d in ds if d and d <= today_s]" not in tail:
        raise SystemExit("Safe Seoul registration-date fallback marker missing")
    SUPPORT_CRAWLER.write_text(head + tail, encoding="utf-8")


def main():
    harden_seoul_registration_fallback()

    text = TARGET.read_text(encoding="utf-8")
    if "import crawl_gyeonggi_central_recent as central_recent" not in text:
        marker = "import scrape_jobs as primary\n"
        if marker not in text:
            raise SystemExit("reconcile_source_ids.py primary import not found")
        text = text.replace(marker, marker + "import crawl_gyeonggi_central_recent as central_recent\n", 1)

    old = "gg_central = primary.scrape_gyeonggi_central()"
    new = "gg_central = central_recent.scrape_gyeonggi_central_recent()"
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise SystemExit("Gyeonggi central reconciliation call not found")

    if "RECONCILIATION_POLICY =" not in text:
        marker = "NOW_S = NOW.strftime(\"%Y-%m-%d %H:%M:%S KST\")\n"
        if marker not in text:
            raise SystemExit("reconciliation timestamp marker not found")
        text = text.replace(marker, marker + f'RECONCILIATION_POLICY = "{POLICY}"\n', 1)

    summary_marker = '        "generatedAt": NOW_S, "lookbackDays": cov.LOOKBACK_DAYS,\n'
    summary_repl = summary_marker + '        "populationPolicy": RECONCILIATION_POLICY,\n'
    if '"populationPolicy": RECONCILIATION_POLICY' not in text:
        if summary_marker not in text:
            raise SystemExit("sourceReconciliation summary marker not found")
        text = text.replace(summary_marker, summary_repl, 1)

    ledger_marker = '        "generatedAt": NOW_S,\n        "policy": "append-only stable official posting IDs across all 38 sources; title similarity never deletes source evidence",\n'
    ledger_repl = '        "generatedAt": NOW_S,\n        "populationPolicy": RECONCILIATION_POLICY,\n        "policy": "append-only stable official posting IDs across all 38 sources; title similarity never deletes source evidence",\n'
    if ledger_marker in text:
        text = text.replace(ledger_marker, ledger_repl, 1)

    report_marker = '        "generatedAt": NOW_S, "lookbackDays": cov.LOOKBACK_DAYS,\n        "summary": payload["sourceReconciliation"],\n'
    report_repl = '        "generatedAt": NOW_S, "lookbackDays": cov.LOOKBACK_DAYS,\n        "populationPolicy": RECONCILIATION_POLICY,\n        "summary": payload["sourceReconciliation"],\n'
    if report_marker in text:
        text = text.replace(report_marker, report_repl, 1)

    TARGET.write_text(text, encoding="utf-8")
    print(f"Reconciliation uses full recent 90-day Gyeonggi central population; policy={POLICY}; Seoul future registration fallback blocked")


if __name__ == "__main__":
    main()
