#!/usr/bin/env python3
"""Patch source-ID reconciliation to use the 90-day Gyeonggi central crawler.

This is idempotent and deliberately limited to reconciliation. The fast collector may keep the
official UI's active-oriented view, while the completeness proof must include recently closed
postings throughout the 90-day retention window.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "reconcile_source_ids.py"


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

    TARGET.write_text(text, encoding="utf-8")
    print("Reconciliation now uses full recent 90-day Gyeonggi central population")


if __name__ == "__main__":
    main()
