#!/usr/bin/env python3
"""Patch source-ID reconciliation to use the 90-day Gyeonggi central crawler.

This is idempotent and deliberately limited to reconciliation. The fast collector may keep the
official UI's active-oriented view, while the completeness proof must include recently closed
postings throughout the 90-day retention window. The patch also stamps a policy version into
all reconciliation artifacts so pre-90-day reports cannot be reused as authoritative evidence.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "reconcile_source_ids.py"
POLICY = "stable-id-38-v2-gyeonggi-central-90d"


def main():
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
    print(f"Reconciliation uses full recent 90-day Gyeonggi central population; policy={POLICY}")


if __name__ == "__main__":
    main()
